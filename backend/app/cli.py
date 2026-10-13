"""Data CLI. Run from backend/:  python -m app.cli <command> --help
None of these commands call the AI model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import db, demo
from .connectors import gleif, gleif_files, icij, mca, nse, opensanctions, opensanctions_csv, samples
from .connectors.crawler import get_client
from .graph.analytics import all_leads
from .intel.service import investigate
from .resolution import matcher


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="create tables (idempotent)")
    sub.add_parser("stats", help="row counts per table")
    s = sub.add_parser("gleif-search", help="live GLEIF full-text search (does not store anything)")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    c = sub.add_parser("gleif-crawl", help="store LEI records + parent/child relationships around seed LEIs")
    c.add_argument("leis", nargs="+")
    c.add_argument("--depth", type=int, default=1)
    c.add_argument("--max-records", type=int, default=200)
    c.add_argument("--include-ultimate", action="store_true")
    o = sub.add_parser("os-load", help="load an OpenSanctions FtM dataset (download is cached + skipped if unchanged)")
    o.add_argument("--dataset", default="us_ofac_sdn")
    o.add_argument("--file", type=Path, help="use a local entities.ftm.json instead of downloading")
    o.add_argument("--force", action="store_true")
    m = sub.add_parser("mca-load", help="load MCA Company Master Data CSV file(s)")
    m.add_argument("files", nargs="+", type=Path)
    m.add_argument("--source-url", help="where the file was downloaded from (stored as provenance)")
    g = sub.add_parser("gleif-file", help="load GLEIF Level 1 (LEI-CDF) and/or Level 2 (RR-CDF) XML files")
    g.add_argument("--level1", type=Path)
    g.add_argument("--level2", type=Path)
    i = sub.add_parser("icij-load", help="load ICIJ Offshore Leaks CSVs from a folder")
    i.add_argument("folder", type=Path)
    n = sub.add_parser("nse-load", help="load NSE debarred-entity lists (xls / xlsx / csv)")
    n.add_argument("files", nargs="+", type=Path)
    n.add_argument("--source-url")
    oc = sub.add_parser("os-csv-load", help="load an OpenSanctions targets.simple.csv")
    oc.add_argument("file", type=Path)
    sl = sub.add_parser("samples-load", help="load every dataset found in samples.zip (or an extracted folder)")
    sl.add_argument("path", type=Path)
    sub.add_parser("crawler-health", help="GET /health on CRAWLER_URL (no Serper credits)")
    sub.add_parser("crawler-usage", help="crawler attempts, cache hits and the Serper credit upper bound")
    inv = sub.add_parser("investigate", help="resolve a CIN / LEI / DIN / PAN / name; --crawl spends crawler credits")
    inv.add_argument("query")
    inv.add_argument("--entity")
    inv.add_argument("--crawl", action="store_true")
    inv.add_argument("--force-refresh", action="store_true")
    r = sub.add_parser("screen", help="identifier joins (auto) + fuzzy name candidates (review queue)")
    r.add_argument("--no-fuzzy", action="store_true")
    r.add_argument("--threshold", type=float, default=matcher.FUZZY_THRESHOLD)
    r.add_argument("--source", default="gleif", help="fuzzy: entities from this source ...")
    r.add_argument("--target", default="opensanctions", help="... are compared with this source")
    v = sub.add_parser("review", help="confirm/reject/reset a match candidate")
    v.add_argument("match_id", type=int)
    v.add_argument("decision", choices=["confirm", "reject", "reset"])
    v.add_argument("--note")
    lead = sub.add_parser("leads", help="print ranked investigation leads")
    lead.add_argument("--type")
    d = sub.add_parser("demo", help="load (or --remove) the synthetic demo fixture")
    d.add_argument("--remove", action="store_true")
    sub.add_parser("duckdb-init", help="initialize DuckDB / MotherDuck schema")
    sub.add_parser("duckdb-stats", help="row counts and sources from DuckDB / MotherDuck")
    dimp = sub.add_parser("duckdb-import", help="import dataset or samples into DuckDB / MotherDuck")
    dimp.add_argument("path", type=Path, help="path to samples.zip or datasets directory")
    args = p.parse_args(argv)

    conn = db.connect()
    db.init_db(conn)
    if args.cmd == "init-db":
        _print({"db": str(db.settings.db_path), "counts": db.counts(conn)})
    elif args.cmd == "stats":
        _print(db.counts(conn))
    elif args.cmd == "gleif-search":
        _print(gleif.search(args.query, args.limit))
    elif args.cmd == "gleif-crawl":
        _print(gleif.crawl(conn, [x.upper() for x in args.leis], depth=args.depth, max_records=args.max_records,
                           include_ultimate=args.include_ultimate))
    elif args.cmd == "os-load":
        _print(opensanctions.load(conn, args.dataset, file=args.file, force=args.force))
    elif args.cmd == "mca-load":
        _print([mca.load(conn, f, source_url=args.source_url) for f in args.files])
    elif args.cmd == "gleif-file":
        out = {}
        if args.level1:
            out["level1"] = gleif_files.load_level1(conn, args.level1)
        if args.level2:
            out["level2"] = gleif_files.load_level2(conn, args.level2)
        _print(out or {"error": "pass --level1 and/or --level2"})
    elif args.cmd == "icij-load":
        _print(icij.load(conn, args.folder))
    elif args.cmd == "nse-load":
        _print([nse.load(conn, f, source_url=args.source_url) for f in args.files])
    elif args.cmd == "os-csv-load":
        _print(opensanctions_csv.load(conn, args.file))
    elif args.cmd == "samples-load":
        _print(samples.load_all(conn, args.path))
    elif args.cmd == "crawler-health":
        _print(get_client().health())
    elif args.cmd == "crawler-usage":
        _print(get_client().usage())
    elif args.cmd == "investigate":
        _print(investigate(conn, args.query, entity_id=args.entity, crawl=args.crawl, force_refresh=args.force_refresh))
    elif args.cmd == "screen":
        _print(matcher.screen(conn, source=args.source, target=args.target, fuzzy=not args.no_fuzzy,
                              threshold=args.threshold))
    elif args.cmd == "review":
        _print(matcher.review(conn, args.match_id, args.decision, args.note))
    elif args.cmd == "leads":
        _print(all_leads(conn, args.type))
    elif args.cmd == "demo":
        _print({"removed": demo.remove(conn)} if args.remove else demo.seed(conn))
    elif args.cmd == "duckdb-init":
        from . import duckdb_db
        con = duckdb_db.connect()
        duckdb_db.init_db(con)
        _print({"target": duckdb_db.get_connection_string(), "counts": duckdb_db.counts(con)})
    elif args.cmd == "duckdb-stats":
        from . import duckdb_db
        con = duckdb_db.connect()
        duckdb_db.init_db(con)
        _print({"counts": duckdb_db.counts(con), "sources": duckdb_db.get_sources_summary(con)})
    elif args.cmd == "duckdb-import":
        from . import duckdb_db
        from .importers.duckdb_import import import_all_datasets
        con = duckdb_db.connect()
        duckdb_db.init_db(con)
        _print(import_all_datasets(con, args.path))


if __name__ == "__main__":
    main()
