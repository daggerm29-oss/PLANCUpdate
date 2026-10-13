"""Google Cloud BigQuery CLI for PLANC.

Usage:
  python -m app.bigquery_cli init --project <GCP_PROJECT> --dataset planc
  python -m app.bigquery_cli stats --project <GCP_PROJECT> --dataset planc
  python -m app.bigquery_cli import --dir <PATH> --project <GCP_PROJECT> --dataset planc
  python -m app.bigquery_cli search <QUERY> --project <GCP_PROJECT> --dataset planc
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import bigquery_db
from .importers.bigquery_import import import_all_datasets_bigquery
from .settings import settings


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="planc-bigquery", description="PLANC Google Cloud BigQuery CLI")
    p.add_argument("--project", default="", help="Google Cloud Project ID")
    p.add_argument("--dataset", default="planc", help="BigQuery Dataset ID (default: planc)")
    p.add_argument("--location", default="US", help="BigQuery Dataset Region (default: US)")

    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="Create dataset and clustered tables")
    sub.add_parser("stats", help="Display table row counts")

    imp = sub.add_parser("import", help="Stream and batch import datasets into BigQuery")
    imp.add_argument("--dir", type=Path, required=True, help="Path to datasets directory")

    s = sub.add_parser("search", help="Search entities in BigQuery")
    s.add_argument("query", help="Entity name, CIN, LEI, PAN, or DIN")
    s.add_argument("--limit", type=int, default=10)

    args = p.parse_args(argv)

    project_id = args.project or settings.gcp_project_id or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not project_id:
        print("Error: GCP Project ID is required. Pass --project <ID> or set GCP_PROJECT_ID environment variable.")
        sys.exit(1)

    client = bigquery_db.get_client(project=project_id, location=args.location)

    if args.cmd == "init":
        print(f"Initializing BigQuery dataset `{project_id}.{args.dataset}`...")
        bigquery_db.init_dataset_and_tables(client, dataset_id=args.dataset)
        print("Dataset and clustered tables successfully initialized.")
        _print(bigquery_db.counts(client, dataset_id=args.dataset))

    elif args.cmd == "stats":
        print(f"Fetching table counts for `{project_id}.{args.dataset}`...")
        _print(bigquery_db.counts(client, dataset_id=args.dataset))

    elif args.cmd == "import":
        target_dir = Path(args.dir)
        if not target_dir.exists():
            print(f"Error: Dataset directory '{target_dir}' does not exist.")
            sys.exit(1)
        start_t = time.time()
        results = import_all_datasets_bigquery(client, target_dir, dataset_id=args.dataset)
        elapsed = round(time.time() - start_t, 2)
        print(f"Import process finished in {elapsed}s.")
        _print({
            "elapsed_seconds": elapsed,
            "results": results,
            "counts": bigquery_db.counts(client, dataset_id=args.dataset),
        })

    elif args.cmd == "search":
        print(f"Searching for '{args.query}' in `{project_id}.{args.dataset}`...")
        results = bigquery_db.search(client, args.query, limit=args.limit, dataset_id=args.dataset)
        print(f"Found {len(results)} match(es).")
        _print(results)


if __name__ == "__main__":
    main()
