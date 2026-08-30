// Shared DuckDB-WASM bootstrap. Every page (index.html, explore.html,
// domain.html) imports initDb() and gets back a connection with a
// `v_current` view already registered over the Parquet snapshot — same
// relationship pwo/api.py has to the real v_current view, just computed
// client-side instead of read from MotherDuck.

import * as duckdb from "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.28.0/+esm";

// Data refresh is decoupled from app deploy: the daily crawl workflow
// re-uploads v_current.parquet/tech_top.json to the `latest` GitHub
// Release, so the site never needs to redeploy just because the data
// changed. Locally, keep reading the checked-in-but-gitignored
// site/data/ path instead, so `python -m http.server` + compact.py still
// works exactly as before this pointed at a real release.
export const DATA_BASE_URL =
  location.hostname === "localhost" || location.hostname === "127.0.0.1"
    ? "data"
    : "https://github.com/mtworth/publicwebobservatory/releases/download/latest";

let dbPromise = null;

export function initDb() {
  if (!dbPromise) dbPromise = boot();
  return dbPromise;
}

async function boot() {
  const bundles = duckdb.getJsDelivrBundles();
  const bundle = await duckdb.selectBundle(bundles);
  const workerUrl = URL.createObjectURL(
    new Blob([`importScripts("${bundle.mainWorker}");`], { type: "text/javascript" })
  );
  const worker = new Worker(workerUrl);
  const logger = new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING);
  const db = new duckdb.AsyncDuckDB(logger, worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  URL.revokeObjectURL(workerUrl);

  await db.registerFileURL(
    "v_current.parquet",
    new URL(`${DATA_BASE_URL}/v_current.parquet`, location.href).href,
    duckdb.DuckDBDataProtocol.HTTP,
    false
  );

  const conn = await db.connect();
  await conn.query(`CREATE VIEW v_current AS SELECT * FROM parquet_scan('v_current.parquet')`);
  return { db, conn };
}

// Small helpers mirroring pwo/api.py's Jinja filters / formatting helpers.

export function formatInt(n) {
  return Number(n).toLocaleString();
}

export function timeAgo(iso) {
  if (!iso) return "";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "";
  const diff = Math.floor((Date.now() - dt.getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// Mirrors api.py's _signal_for(): pick the single most interesting fact
// about a domain to show in the home page feed.
export function signalFor(d) {
  const blockType = d.homepage_block_type;
  if (blockType && blockType !== "none") {
    return [`blocked · ${blockType.replaceAll("_", " ")}`, "warn"];
  }
  const expiry = d.tls_days_until_expiry;
  if (expiry !== null && expiry !== undefined && Number(expiry) < 30) {
    return [`TLS expires in ${expiry}d`, "warn"];
  }
  if (d.llms_txt_available) return ["serves llms.txt", "ok"];
  const technologies = d.technologies || [];
  if (technologies.length) return [technologies.slice(0, 2).join(" · "), ""];
  if (d.dns_resolves === false) return ["DNS not resolving", "fail"];
  if (d.https_available && d.tls_valid) return ["HTTPS · valid TLS", "ok"];
  if (d.https_available && !d.tls_valid) return ["HTTPS · invalid TLS", "warn"];
  return [d.collection_status || "checked", ""];
}

export function escapeSqlLiteral(s) {
  return String(s).replace(/'/g, "''");
}

// Arrow's row.toJSON() doesn't deep-convert nested list columns (e.g.
// `technologies`) into plain JS arrays — they come back as Arrow Vector
// objects that look array-like but don't support .map()/.slice()/.join().
// Convert those explicitly so callers can treat every field as plain data.
export function rowsToObjects(result) {
  return result.toArray().map((r) => {
    const obj = r.toJSON ? r.toJSON() : r;
    for (const key of Object.keys(obj)) {
      const val = obj[key];
      if (val && typeof val === "object" && typeof val.toArray === "function") {
        obj[key] = Array.from(val.toArray());
      }
    }
    return obj;
  });
}
