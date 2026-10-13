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

    s = sub.add_parser("search", help="Test search against DuckDB / MotherDuck")
    s.add_argument("query", help="Entity name, CIN, LEI, PAN, or DIN")
    s.add_argument("--limit", type=int, default=10)

    args = p.parse_args(argv)

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
        results = import_all_datasets(con, Path(import_path))
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
