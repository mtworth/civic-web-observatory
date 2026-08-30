"""
Writes a batch of crawl results directly to a Parquet file — the
MotherDuck-free output path for the static-site pipeline (see
testmigration/README.md). Same one-row-per-observation shape as the
`observations` table in db.py's insert_observation(), just serialized
straight to a file instead of inserted into a database. No DB connection,
no MOTHERDUCK_TOKEN, nothing but DuckDB used as a local Parquet writer.

Intended usage: one file per crawl run (a day's slice), e.g.
data/observations/<date>.parquet — an immutable partition compact.py
later reads alongside every other day's partition to rebuild the latest-
per-domain snapshot.
"""

import json
import tempfile
import typing
from datetime import datetime
from pathlib import Path

import duckdb

from .models import DomainObservation


def _json_default(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"not JSON serializable: {type(obj)}")


def _duckdb_type_for(annotation) -> str:
    """Map a DomainObservation field's Python type annotation to a DuckDB
    type, so every partition this writes has an identical, correct schema
    regardless of which columns happen to be all-NULL in a given batch —
    read_json_auto()'s own type inference can't be trusted for that (an
    all-null column with no other signal gets inferred as JSON/NULL type,
    not VARCHAR, and then clashes with every other partition's schema)."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union:  # Optional[X] == Union[X, None]
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        annotation = args[0] if args else str
        origin = typing.get_origin(annotation)
    if origin is list:
        return "VARCHAR[]"  # every list field on this model is list[str]
    if annotation is bool:
        return "BOOLEAN"
    if annotation is int:
        return "INTEGER"
    if annotation is datetime:
        return "TIMESTAMPTZ"
    return "VARCHAR"  # str, and the fallback for anything unanticipated


def _select_columns_sql() -> str:
    parts = []
    for name, field in DomainObservation.model_fields.items():
        duckdb_type = _duckdb_type_for(field.annotation)
        parts.append(f'"{name}"::{duckdb_type} AS "{name}"')
    return ", ".join(parts)


def write_observations_parquet(observations: list[DomainObservation], path: str) -> None:
    """Write a batch of observations to a single Parquet file at `path`."""
    if not observations:
        print(f"no observations to write — skipping {path}")
        return

    Path(path).parent.mkdir(parents=True, exist_ok=True)

    # DuckDB's Python replacement scan doesn't accept a plain list of
    # dicts (only DataFrame/Arrow/DuckDBPyRelation), and this project has
    # no pandas/pyarrow dependency — round-trip through NDJSON instead.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False) as f:
        for obs in observations:
            f.write(json.dumps(obs.model_dump(), default=_json_default) + "\n")
        ndjson_path = f.name

    try:
        con = duckdb.connect()
        safe_json_path = ndjson_path.replace("'", "''")
        safe_out_path = str(path).replace("'", "''")
        columns_sql = _select_columns_sql()
        con.execute(
            f"COPY (SELECT {columns_sql} FROM read_json_auto('{safe_json_path}')) "
            f"TO '{safe_out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        con.close()
    finally:
        Path(ndjson_path).unlink(missing_ok=True)
