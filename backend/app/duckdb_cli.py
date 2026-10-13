"""Standalone DuckDB & MotherDuck CLI for dataset management and import.

Usage:
  # Initialize DuckDB / MotherDuck schema
  python -m app.duckdb_cli init

  # Import sample datasets from samples.zip into local DuckDB
  python -m app.duckdb_cli import --samples ../samples.zip

  # Import full 1.14 GB datasets into MotherDuck (uses MOTHERDUCK_TOKEN)
  python -m app.duckdb_cli import --dir /path/to/full/datasets --target motherduck

  # Show table row counts and ingestion statistics
  python -m app.duckdb_cli stats

  # Test search query
  python -m app.duckdb_cli search "TATA"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .duckdb_db import connect, counts, get_sources_summary, init_db, is_motherduck, search
from .importers.duckdb_import import import_all_datasets
from .settings import settings


def _print(data: any) -> None:
    print(json.dumps(data, indent=2, default=str, ensure_ascii=False))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="python -m app.duckdb_cli",
        description="PLANC DuckDB & MotherDuck data pipeline CLI"
    )
    p.add_argument(
        "--target",
        choices=["auto", "motherduck", "local"],
        default="auto",
        help="Database target (auto detects from MOTHERDUCK_TOKEN)",
    )
    p.add_argument(
        "--token",
        help="MotherDuck token override (otherwise reads MOTHERDUCK_TOKEN env var)",
    )
    p.add_argument(
        "--db",
        help="MotherDuck database name or local file path",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    # Subcommands
    sub.add_parser("init", help="Create tables and seed initial sources")
    sub.add_parser("stats", help="Display table row counts and sources")

    imp = sub.add_parser("import", help="Stream and batch import datasets")
    imp.add_argument("--samples", type=Path, help="Path to samples.zip or samples directory")
    imp.add_argument("--dir", type=Path, help="Path to full datasets directory")
    imp.add_argument("--file", type=Path, help="Path to a single dataset file")
    imp.add_argument("--skip-gleif", action="store_true", help="Skip heavy GLEIF XML archives to import fast datasets first")
    imp.add_argument("--only", choices=["mca", "opensanctions", "nse", "icij", "gleif"], help="Only import specific dataset type")

    psh = sub.add_parser("push", help="Push local DuckDB tables directly to MotherDuck in one fast command")
    psh.add_argument("--from-local", type=Path, default=Path("planc.duckdb"), help="Local DuckDB database file path")
    psh.add_argument("--to-md", type=str, default="planc", help="Target MotherDuck database name")

    s = sub.add_parser("search", help="Test search against DuckDB / MotherDuck")
    s.add_argument("query", help="Entity name, CIN, LEI, PAN, or DIN")
    s.add_argument("--limit", type=int, default=10)

    to_pq = sub.add_parser("to-parquet", help="Convert local datasets to Hugging Face Parquet format")
    to_pq.add_argument("--dir", type=Path, required=True, help="Path to local datasets directory")
    to_pq.add_argument("--out", type=Path, default=Path("./parquet_export"), help="Output directory for Parquet files")
    to_pq.add_argument("--skip-gleif", action="store_true", help="Skip heavy GLEIF XML archives")
    to_pq.add_argument("--batch-size", type=int, default=50000, help="Batch insertion size")

    args = p.parse_args(argv)

    if args.cmd == "to-parquet":
        from .convert_to_parquet import ParquetConverter
        root = args.dir.resolve()
        if not root.exists():
            print(f"Error: Datasets directory '{root}' does not exist.")
            sys.exit(1)
        converter = ParquetConverter(output_dir=args.out.resolve(), batch_size=args.batch_size)
        for mca_path in root.glob("mca/*.csv"):
            converter.process_mca(mca_path)
        for os_path in root.glob("opensanctions/*.csv"):
            converter.process_opensanctions(os_path)
        if not args.skip_gleif:
            for g1 in list(root.glob("gleif/level1.zip")) + list(root.glob("gleif/level1/*.xml")):
                converter.process_gleif_level1(g1)
            for g2 in list(root.glob("gleif/level2.zip")) + list(root.glob("gleif/level2/*.xml")):
                converter.process_gleif_level2(g2)
        converter.export_final_parquets()
        return

    if args.cmd == "push":
        local_path = args.from_local
        if not local_path.exists():
            print(f"Error: Local DuckDB database file '{local_path}' does not exist.")
            sys.exit(1)
        md_name = args.to_md or "planc"
        token = args.token or os.environ.get("MOTHERDUCK_TOKEN", "")
        token_clause = f"?motherduck_token={token}" if token else ""
        print(f"Opening local database: {local_path}...")
        local_con = connect(str(local_path))
        print(f"Connecting and attaching MotherDuck cloud database: md:{md_name}...")
        local_con.execute(f"ATTACH 'md:{md_name}{token_clause}' AS md_target;")
        tables = ["sources", "entities", "identifiers", "relationships", "ingest_runs"]
        for tbl in tables:
            try:
                exists = local_con.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [tbl]
                ).fetchone()
                if exists:
                    print(f"  -> Uploading table '{tbl}' to MotherDuck...")
                    local_con.execute(f"CREATE OR REPLACE TABLE md_target.{tbl} AS SELECT * FROM {tbl};")
                    cnt = local_con.execute(f"SELECT count(*) FROM md_target.{tbl};").fetchone()[0]
                    print(f"     [SUCCESS] {cnt:,} records uploaded into md:{md_name}.{tbl}")
            except Exception as e:
                print(f"     [WARNING] Could not upload table {tbl}: {e}")
        print("\nAll local tables successfully pushed to MotherDuck!")
        return

    # Resolve connection target
    target_mode = args.target
    if args.token:
        os.environ["MOTHERDUCK_TOKEN"] = args.token

    conn_str = None
    if target_mode == "motherduck" or (target_mode == "auto" and os.environ.get("MOTHERDUCK_TOKEN")):
        db_name = args.db or settings.motherduck_database or "planc"
        token = args.token or os.environ.get("MOTHERDUCK_TOKEN", "")
        conn_str = f"md:{db_name}?motherduck_token={token}" if token else f"md:{db_name}"
        print(f"Connecting to MotherDuck cloud database: md:{db_name}...")
    elif target_mode == "local" or target_mode == "auto":
        db_path = Path(args.db) if args.db else settings.duckdb_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn_str = str(db_path)
        print(f"Connecting to local DuckDB database: {db_path}...")

    con = connect(conn_str)

    if args.cmd == "init":
        init_db(con)
        print("Schema successfully initialized.")
        _print({"target": conn_str, "counts": counts(con)})

    elif args.cmd == "stats":
        init_db(con)
        table_counts = counts(con)
        sources = get_sources_summary(con)
        _print({
            "target": conn_str,
            "counts": table_counts,
            "sources": sources,
        })

    elif args.cmd == "import":
        init_db(con)
        start_time = time.time()
        import_path = args.samples or args.dir or args.file
        if not import_path:
            # Check default samples.zip locations
            for candidate in [
                Path("samples.zip"),
                Path("../samples.zip"),
                Path("backend/data/samples.zip"),
            ]:
                if candidate.is_file():
                    import_path = candidate
                    break

        if not import_path or not Path(import_path).exists():
            print(f"Error: Dataset path '{import_path}' not found. Specify with --samples, --dir, or --file.")
            sys.exit(1)

        print(f"Starting repeatable import from: {import_path}")
        skip_gleif = getattr(args, "skip_gleif", False)
        only = getattr(args, "only", None)
        results = import_all_datasets(con, Path(import_path), skip_gleif=skip_gleif, only=only)
        elapsed = round(time.time() - start_time, 2)
        print(f"Import completed in {elapsed}s.")
        _print({
            "elapsed_seconds": elapsed,
            "dataset_results": results,
            "final_counts": counts(con),
        })

    elif args.cmd == "search":
        res = search(con, args.query, limit=args.limit)
        print(f"Found {len(res)} results for query: {args.query!r}")
        _print(res)


if __name__ == "__main__":
    main()
