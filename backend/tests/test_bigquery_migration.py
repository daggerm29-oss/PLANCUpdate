"""Integration and Proof-of-Concept tests for Google Cloud BigQuery migration."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.bigquery_db import SOURCES, dumps, loads
from app.importers.bigquery_import import (
    classify_dataset_file,
    deterministic_rel_id,
    import_gleif_level1_bigquery,
    import_gleif_level2_bigquery,
    import_mca_bigquery,
)


class MockBigQueryClient:
    """In-memory mock for Google Cloud BigQuery client that emulates dataset operations."""

    def __init__(self, project: str = "planc-test-project"):
        self.project = project
        self.tables: dict[str, list[dict]] = {
            "sources": [],
            "ingest_runs": [],
            "raw_records": [],
            "entities": [],
            "entity_names": [],
            "identifiers": [],
            "relationships": [],
            "evidence": [],
            "match_candidates": [],
            "claims": [],
        }

    def insert_rows_json(self, table_id: str, rows: list[dict]) -> list:
        tbl_name = table_id.split(".")[-1]
        if tbl_name not in self.tables:
            self.tables[tbl_name] = []
        # Merge/replace based on 'id' if present
        existing = {r.get("id"): idx for idx, r in enumerate(self.tables[tbl_name]) if "id" in r}
        for r in rows:
            if "id" in r and r["id"] in existing:
                self.tables[tbl_name][existing[r["id"]]] = r
            else:
                self.tables[tbl_name].append(r)
                if "id" in r:
                    existing[r["id"]] = len(self.tables[tbl_name]) - 1
        return []

    def query(self, sql: str, job_config: Any = None) -> Any:
        mock_job = MagicMock()
        mock_job.result.return_value = []
        return mock_job


class TestBigQueryPOC(unittest.TestCase):
    def test_sources_and_deterministic_rel_id(self):
        """Verify sources structure and deterministic relationship IDs."""
        self.assertGreaterEqual(len(SOURCES), 5)
        id1 = deterministic_rel_id("gleif", "subj1", "parent", "obj1")
        id2 = deterministic_rel_id("gleif", "subj1", "parent", "obj1")
        self.assertEqual(id1, id2)
        self.assertGreater(id1, 0)

    def test_dataset_classification_matching_user_tree(self):
        """Verify that all files in user's directory tree are accurately classified."""
        self.assertEqual(classify_dataset_file(Path("datasets/mca/andhra_pradesh.csv")), "mca")
        self.assertEqual(classify_dataset_file(Path("datasets/mca/karnataka.csv")), "mca")
        self.assertEqual(classify_dataset_file(Path("datasets/gleif/level1.zip")), "gleif_level1")
        self.assertEqual(classify_dataset_file(Path("datasets/gleif/level2.zip")), "gleif_level2")
        self.assertEqual(classify_dataset_file(Path("datasets/icij/full-oldb.LATEST.zip")), "icij")
        self.assertEqual(classify_dataset_file(Path("datasets/opensanctions/targets.simple.csv")), "opensanctions")
        self.assertEqual(classify_dataset_file(Path("datasets/nse/prs_ra_others.xls")), "nse")
        self.assertEqual(classify_dataset_file(Path("datasets/nse/prs_ra_sebi.xls")), "nse")

    def test_poc_mca_csv_import_into_bigquery(self):
        """POC: Test streaming ingestion of synthetic MCA CSV into BigQuery mock."""
        mock_client = MockBigQueryClient()
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "andhra_pradesh.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["CIN", "Company Name", "Company Status", "Company State", "Company Address", "Authorized Capital", "Paidup Capital"])
                writer.writerow(["U12345AP2020PTC123456", "HYDERABAD TECH SOLUTIONS PRIVATE LIMITED", "ACTIVE", "Andhra Pradesh", "Plot 1, Hitec City", "1000000", "500000"])

            counts = import_mca_bigquery(mock_client, csv_path, "planc")
            self.assertEqual(counts["seen"], 1)
            self.assertEqual(counts["inserted"], 1)
            self.assertEqual(len(mock_client.tables["entities"]), 1)
            ent = mock_client.tables["entities"][0]
            self.assertEqual(ent["id"], "mca:U12345AP2020PTC123456")
            self.assertEqual(ent["name"], "HYDERABAD TECH SOLUTIONS PRIVATE LIMITED")
            self.assertEqual(ent["country"], "in")

            # Test idempotent re-import
            counts2 = import_mca_bigquery(mock_client, csv_path, "planc")
            self.assertEqual(len(mock_client.tables["entities"]), 1)  # No duplicates

    def test_poc_gleif_streaming_zip_import_into_bigquery(self):
        """POC: Test streaming ingestion of synthetic GLEIF Level 1 and Level 2 ZIPs."""
        mock_client = MockBigQueryClient()
        with tempfile.TemporaryDirectory() as tmpdir:
            # Level 1 ZIP
            l1_zip = Path(tmpdir) / "level1.zip"
            l1_xml = b"""<?xml version="1.0" encoding="utf-8"?>
            <lei:LEIData xmlns:lei="http://www.gleif.org/data/schema/leidata/2016">
              <lei:LEIRecords>
                <lei:LEIRecord>
                  <lei:LEI>5493006MHB84DD0ZWV18</lei:LEI>
                  <lei:Entity>
                    <lei:LegalName>STATE BANK OF INDIA</lei:LegalName>
                    <lei:LegalAddress><lei:Country>IN</lei:Country><lei:City>Mumbai</lei:City></lei:LegalAddress>
                  </lei:Entity>
                </lei:LEIRecord>
              </lei:LEIRecords>
            </lei:LEIData>"""
            with zipfile.ZipFile(l1_zip, "w") as zf:
                zf.writestr("2026-gleif-level1.xml", l1_xml)

            c1 = import_gleif_level1_bigquery(mock_client, l1_zip, "planc")
            self.assertEqual(c1["seen"], 1)
            self.assertEqual(c1["inserted"], 1)
            self.assertEqual(mock_client.tables["entities"][0]["id"], "gleif:5493006MHB84DD0ZWV18")

            # Level 2 ZIP
            l2_zip = Path(tmpdir) / "level2.zip"
            l2_xml = b"""<?xml version="1.0" encoding="utf-8"?>
            <rr:RelationshipData xmlns:rr="http://www.gleif.org/data/schema/rr/2016">
              <rr:RelationshipRecords>
                <rr:RelationshipRecord>
                  <rr:Relationship>
                    <rr:StartNode><rr:NodeID>549300SUB12345678901</rr:NodeID></rr:StartNode>
                    <rr:EndNode><rr:NodeID>5493006MHB84DD0ZWV18</rr:NodeID></rr:EndNode>
                    <rr:RelationshipType>IS_DIRECTLY_CONSOLIDATED_BY</rr:RelationshipType>
                  </rr:Relationship>
                </rr:RelationshipRecord>
              </rr:RelationshipRecords>
            </rr:RelationshipData>"""
            with zipfile.ZipFile(l2_zip, "w") as zf:
                zf.writestr("2026-gleif-level2.xml", l2_xml)

            c2 = import_gleif_level2_bigquery(mock_client, l2_zip, "planc")
            self.assertEqual(c2["seen"], 1)
            self.assertEqual(c2["inserted"], 1)
            rel = mock_client.tables["relationships"][0]
            self.assertEqual(rel["subject_id"], "gleif:549300SUB12345678901")
            self.assertEqual(rel["object_id"], "gleif:5493006MHB84DD0ZWV18")
            self.assertEqual(rel["predicate"], "direct_parent")


if __name__ == "__main__":
    unittest.main()
