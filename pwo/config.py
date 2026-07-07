from dataclasses import dataclass, field


@dataclass
class Config:
    concurrency: int = 10
    timeout: int = 15
    max_response_bytes: int = 500_000
    crawler_version: str = "0.1.0"
    user_agent: str = (
        "CivicWebIndexBot/0.1 "
        "(+https://civicwebindex.org/bot; contact: hello@civicwebindex.org)"
    )
    output_dir: str = "outputs"
    db_path: str = "outputs/observations.duckdb"
