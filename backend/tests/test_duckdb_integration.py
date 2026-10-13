"""Integration and unit tests for DuckDB & MotherDuck integration."""
from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    import pytest
except ImportError:
    pytest = None


from app import db
from app.duckdb_db import SOURCES, init_db as init_duckdb_schema
from app.importers.duckdb_import import (
    import_all_datasets,
    import_gleif_level1,
    import_gleif_level2,
    import_icij_directory,
    import_mca,
    import_opensanctions_csv,
)
from app.settings import settings


class MockDuckDBConnection:
    """Mock connection that executes queries against an in-memory SQLite store with DuckDB SQL adaptation."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def execute(self, sql: str, params: list | None = None):
        cleaned_sql = (
            sql.replace("BIGINT", "INTEGER")
            .replace("VARCHAR", "TEXT")
            .replace("DOUBLE", "REAL")
            .replace("ILIKE", "LIKE")
        )
        cur = self.conn.cursor()
        if params is not None:
            cur.execute(cleaned_sql, params)
        elif ";" in cleaned_sql.strip() and not cleaned_sql.strip().startswith("SELECT"):
            self.conn.executescript(cleaned_sql)
        else:
            cur.execute(cleaned_sql)
        return cur


    def executemany(self, sql: str, params_list: list):
        cleaned_sql = (
            sql.replace("BIGINT", "INTEGER")
            .replace("VARCHAR", "TEXT")
            .replace("DOUBLE", "REAL")
            .replace("ILIKE", "LIKE")
        )
        return self.conn.executemany(cleaned_sql, params_list)


def test_sources_and_schema_definition():
    """Verify standard OSINT sources are registered."""
    source_ids = {s[0] for s in SOURCES}
    assert "mca" in source_ids
    assert "gleif" in source_ids
    assert "nse" in source_ids
    assert "icij" in source_ids
    assert "opensanctions" in source_ids


def test_mca_csv_import_pipeline():
    """Test MCA Karnataka / Andhra Pradesh CSV parsing and batch upsert."""
    mock_con = MockDuckDBConnection()
    init_duckdb_schema(mock_con)

    with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as f:
        writer = csv.writer(f)
        writer.writerow([
            "CIN", "Company Name", "Company Registration Date", "Company Category",
            "Company Class", "Listing Status", "Authorized Capital", "Paidup Capital",
            "Company ROC", "Company Address", "Pin Code", "Company State",
            "Company Status", "Company Sub Category", "Company Industrial Classification"
        ])
        writer.writerow([
            "U72200KA2020PTC123456", "Bangalore AI Analytics Pvt Ltd", "2020-05-15",
            "Company limited by Shares", "Private", "Unlisted", "1000000", "500000",
            "RoC-Bangalore", "100 Tech Park, Whitefield, 560066-India", "560066",
            "Karnataka", "Active", "Non-govt company", "Computer and related activities"
        ])
        f.flush()
        f_path = Path(f.name)

    try:
        stats = import_mca(mock_con, f_path)
        assert stats["seen"] == 1
        assert stats["inserted"] == 1
        assert stats["rejected"] == 0

        # Verify entity stored in database
        cur = mock_con.execute("SELECT * FROM entities WHERE id = ?", ["mca:U72200KA2020PTC123456"])
        row = cur.fetchone()
        assert row is not None
        assert row["name"] == "Bangalore AI Analytics Pvt Ltd"
        assert row["country"] == "in"
        assert row["jurisdiction"] == "Karnataka"

        # Verify identifier stored
        id_cur = mock_con.execute("SELECT * FROM identifiers WHERE entity_id = ?", ["mca:U72200KA2020PTC123456"])
        id_row = id_cur.fetchone()
        assert id_row is not None
        assert id_row["scheme"] == "cin"
        assert id_row["value"] == "U72200KA2020PTC123456"
    finally:
        f_path.unlink(missing_ok=True)


def test_gleif_level1_and_level2_streaming():
    """Test memory-bounded streaming of Level 1 and Level 2 XML."""
    mock_con = MockDuckDBConnection()
    init_duckdb_schema(mock_con)

    # Level 1 sample XML
    l1_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <LEIData xmlns="http://www.gleif.org/data/schema/leidata/2016">
      <LEIRecords>
        <LEIRecord>
          <LEI>335800QG755100000001</LEI>
          <Entity>
            <LegalName>TATA STEEL LIMITED</LegalName>
            <LegalAddress>
              <FirstAddressLine>Bombay House 24 Homi Mody Street</FirstAddressLine>
              <City>Mumbai</City>
              <Country>IN</Country>
            </LegalAddress>
            <Jurisdiction>IN</Jurisdiction>
            <EntityStatus>ACTIVE</EntityStatus>
          </Entity>
          <Registration>
            <InitialRegistrationDate>2012-10-15T00:00:00Z</InitialRegistrationDate>
            <RegistrationStatus>ISSUED</RegistrationStatus>
          </Registration>
        </LEIRecord>
      </LEIRecords>
    </LEIData>
    """

    # Level 2 sample XML
    l2_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <RRData xmlns="http://www.gleif.org/data/schema/rr/2016">
      <RelationshipRecords>
        <RelationshipRecord>
          <Relationship>
            <StartNode>
              <NodeID>335800QG755100000001</NodeID>
            </StartNode>
            <EndNode>
              <NodeID>335800TATA9999999999</NodeID>
            </EndNode>
            <RelationshipType>IS_ULTIMATELY_CONSOLIDATED_BY</RelationshipType>
          </Relationship>
        </RelationshipRecord>
      </RelationshipRecords>
    </RRData>
    """

    with tempfile.NamedTemporaryFile("w+", suffix=".xml", delete=False) as f1:
        f1.write(l1_xml)
        f1.flush()
        p1 = Path(f1.name)

    with tempfile.NamedTemporaryFile("w+", suffix=".xml", delete=False) as f2:
        f2.write(l2_xml)
        f2.flush()
        p2 = Path(f2.name)

    try:
        s1 = import_gleif_level1(mock_con, p1)
        assert s1["inserted"] == 1

        s2 = import_gleif_level2(mock_con, p2)
        assert s2["inserted"] == 1

        # Check entity
        e_cur = mock_con.execute("SELECT * FROM entities WHERE id = ?", ["gleif:335800QG755100000001"])
        assert e_cur.fetchone()["name"] == "TATA STEEL LIMITED"

        # Check relationship
        r_cur = mock_con.execute(
            "SELECT * FROM relationships WHERE subject_id = ? AND predicate = ?",
            ["gleif:335800QG755100000001", "ultimate_parent"]
        )
        rel = r_cur.fetchone()
        assert rel is not None
        assert rel["object_id"] == "gleif:335800TATA9999999999"
    finally:
        p1.unlink(missing_ok=True)
        p2.unlink(missing_ok=True)


def test_idempotent_reimport():
    """Verify that re-running import does not duplicate records or create integrity errors."""
    mock_con = MockDuckDBConnection()
    init_duckdb_schema(mock_con)

    with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as f:
        writer = csv.writer(f)
        writer.writerow(["id", "schema", "name", "countries", "identifiers", "sanctions"])
        writer.writerow(["sanction-001", "LegalEntity", "Target Trading Ltd", "cy", "CY123456", "EU Sanctions Regulation"])
        f.flush()
        p = Path(f.name)

    try:
        # Run 1
        s1 = import_opensanctions_csv(mock_con, p)
        assert s1["inserted"] == 1

        # Run 2 (re-import)
        s2 = import_opensanctions_csv(mock_con, p)
        assert s2["inserted"] == 1

        # Check total count is exactly 1, not 2
        cnt = mock_con.execute("SELECT COUNT(*) FROM entities WHERE id = 'os:sanction-001'").fetchone()[0]
        assert cnt == 1
    finally:
        p.unlink(missing_ok=True)


def test_health_check_lightweight():
    """Verify health endpoint does not trigger database queries by default."""
    try:
        from fastapi.testclient import TestClient
        from app.api import app
    except ImportError:
        import unittest
        raise unittest.SkipTest("fastapi or testclient not installed in current environment")

    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "healthy"
    assert data["uptime_ping_safe"] is True
    # Without ?db=1, counts is not queried to save MotherDuck compute hours
    assert "counts" not in data


def test_full_synthetic_dataset_directory_structure():
    """Verify that import_all_datasets correctly discovers and processes the exact user folder structure:
    datasets/
    ├── gleif/ (level1.zip, level2.zip)
    ├── icij/ (full-oldb.LATEST.zip)
    ├── mca/ (andhra_pradesh.csv, karnataka.csv)
    ├── nse/ (prs_ra_others.xls, prs_ra_sebi.xls)
    └── opensanctions/ (targets.simple.csv)
    plus reports unrecognized files as skipped, and does not duplicate entities or relationships on re-import.
    """
    import zipfile
    mock_con = MockDuckDBConnection()
    init_duckdb_schema(mock_con)

    with tempfile.TemporaryDirectory() as tmp_dir:
        base = Path(tmp_dir) / "datasets"
        (base / "gleif").mkdir(parents=True)
        (base / "icij").mkdir(parents=True)
        (base / "mca").mkdir(parents=True)
        (base / "nse").mkdir(parents=True)
        (base / "opensanctions").mkdir(parents=True)
        (base / "unrecognized").mkdir(parents=True)

        # 1. GLEIF Level 1 ZIP
        l1_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <LEIData xmlns="http://www.gleif.org/data/schema/leidata/2016">
          <LEIRecords>
            <LEIRecord>
              <LEI>335800QG755100000001</LEI>
              <Entity>
                <LegalName>TATA STEEL LIMITED</LegalName>
                <LegalAddress>
                  <FirstAddressLine>Bombay House 24 Homi Mody Street</FirstAddressLine>
                  <City>Mumbai</City>
                  <Country>IN</Country>
                </LegalAddress>
                <Jurisdiction>IN</Jurisdiction>
                <EntityStatus>ACTIVE</EntityStatus>
              </Entity>
              <Registration>
                <InitialRegistrationDate>2012-10-15T00:00:00Z</InitialRegistrationDate>
                <RegistrationStatus>ISSUED</RegistrationStatus>
              </Registration>
            </LEIRecord>
          </LEIRecords>
        </LEIData>
        """
        with zipfile.ZipFile(base / "gleif" / "level1.zip", "w") as zf:
            zf.writestr("2024-gleif-goldencopy-lei2.xml", l1_xml)

        # 2. GLEIF Level 2 ZIP
        l2_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <RRData xmlns="http://www.gleif.org/data/schema/rr/2016">
          <RelationshipRecords>
            <RelationshipRecord>
              <Relationship>
                <StartNode>
                  <NodeID>335800QG755100000001</NodeID>
                </StartNode>
                <EndNode>
                  <NodeID>335800TATA9999999999</NodeID>
                </EndNode>
                <RelationshipType>IS_ULTIMATELY_CONSOLIDATED_BY</RelationshipType>
              </Relationship>
            </RelationshipRecord>
          </RelationshipRecords>
        </RRData>
        """
        with zipfile.ZipFile(base / "gleif" / "level2.zip", "w") as zf:
            zf.writestr("2024-gleif-goldencopy-rr.xml", l2_xml)

        # 3. ICIJ ZIP
        icij_nodes_csv = "node_id,name,jurisdiction,countries,status,company_type\n99001,Apex Global Ventures Ltd,BVI,VG,Active,Offshore Company\n"
        icij_rels_csv = "_start,_end,link,status,start_date\n99001,99002,shareholder of,Active,2015-01-01\n"
        with zipfile.ZipFile(base / "icij" / "full-oldb.LATEST.zip", "w") as zf:
            zf.writestr("nodes-entities.csv", icij_nodes_csv)
            zf.writestr("relationships.csv", icij_rels_csv)

        # 4. MCA CSVs
        mca_header = "CIN,Company Name,Company Registration Date,Company Category,Company Class,Listing Status,Authorized Capital,Paidup Capital,Company ROC,Company Address,Pin Code,Company State,Company Status,Company Sub Category,Company Industrial Classification\n"
        with open(base / "mca" / "andhra_pradesh.csv", "w", encoding="utf-8") as f:
            f.write(mca_header)
            f.write("U72200AP2020PTC000001,AP Cyber Technologies Pvt Ltd,2020-01-01,Company limited by Shares,Private,Unlisted,1000000,100000,RoC-Vijayawada,Plot 1 Cyber Valley,530001,Andhra Pradesh,Active,Non-govt company,Software\n")

        with open(base / "mca" / "karnataka.csv", "w", encoding="utf-8") as f:
            f.write(mca_header)
            f.write("U72200KA2021PTC000002,Bangalore Cloud Innovations Pvt Ltd,2021-02-02,Company limited by Shares,Private,Unlisted,5000000,500000,RoC-Bangalore,100 MG Road,560001,Karnataka,Active,Non-govt company,Cloud Services\n")

        # 5. NSE files
        nse_content = "Sr No,Order Date,Entity / Individual Name,PAN,DIN,Debarred Period\n1,2021-06-10,Sharma Financial Capital Ltd,AAACS1234F,,3 Years\n"
        with open(base / "nse" / "prs_ra_others.xls", "w", encoding="utf-8") as f:
            f.write(nse_content)
        with open(base / "nse" / "prs_ra_sebi.xls", "w", encoding="utf-8") as f:
            f.write("Sr No,Order Date,Entity / Individual Name,PAN,DIN,Debarred Period\n1,2022-01-15,Verma Securities Broking Pvt Ltd,AAACV9876K,,5 Years\n")

        # 6. OpenSanctions CSV
        os_content = "id,schema,name,countries,identifiers,sanctions\nos-target-100,LegalEntity,Vanguard Baltic Trader,ru,RU1234567,OFAC SDN List\n"
        with open(base / "opensanctions" / "targets.simple.csv", "w", encoding="utf-8") as f:
            f.write(os_content)

        # 7. Unrecognized file
        with open(base / "unrecognized" / "readme.txt", "w", encoding="utf-8") as f:
            f.write("Notes about datasets\n")

        # First run: import all datasets
        summary1 = import_all_datasets(mock_con, base)

        # Verify all expected files are reported
        files_reported = set(summary1.keys())
        assert any("andhra_pradesh.csv" in k for k in files_reported)
        assert any("karnataka.csv" in k for k in files_reported)
        assert any("level1.zip" in k for k in files_reported)
        assert any("level2.zip" in k for k in files_reported)
        assert any("full-oldb.LATEST.zip" in k for k in files_reported)
        assert any("prs_ra_others.xls" in k for k in files_reported)
        assert any("prs_ra_sebi.xls" in k for k in files_reported)
        assert any("targets.simple.csv" in k for k in files_reported)
        assert any("readme.txt" in k for k in files_reported)

        # Verify status of datasets
        for k, v in summary1.items():
            if "readme.txt" in k:
                assert v["status"] == "skipped"
                assert v["category"] == "unknown"
            else:
                assert v["status"] == "success", f"Dataset {k} failed: {v}"

        # Verify row counts in database
        cnt_entities_1 = mock_con.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        cnt_relationships_1 = mock_con.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
        cnt_identifiers_1 = mock_con.execute("SELECT COUNT(*) FROM identifiers").fetchone()[0]

        assert cnt_entities_1 >= 6  # AP, KA, TATA, Apex, Sharma, Verma, Vanguard
        assert cnt_relationships_1 == 2  # 1 from GLEIF + 1 from ICIJ

        # Second run: re-import identical datasets (idempotency check)
        summary2 = import_all_datasets(mock_con, base)
        for k, v in summary2.items():
            if "readme.txt" not in k:
                assert v["status"] == "success"

        cnt_entities_2 = mock_con.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        cnt_relationships_2 = mock_con.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
        cnt_identifiers_2 = mock_con.execute("SELECT COUNT(*) FROM identifiers").fetchone()[0]

        # Verify NO duplicate entities or relationships were created on re-import
        assert cnt_entities_2 == cnt_entities_1, f"Entities duplicated: {cnt_entities_2} vs {cnt_entities_1}"
        assert cnt_relationships_2 == cnt_relationships_1, f"Relationships duplicated: {cnt_relationships_2} vs {cnt_relationships_1}"
        assert cnt_identifiers_2 == cnt_identifiers_1, f"Identifiers duplicated: {cnt_identifiers_2} vs {cnt_identifiers_1}"


import unittest


class TestDuckDBIntegration(unittest.TestCase):
    def test_sources(self):
        test_sources_and_schema_definition()

    def test_mca_import(self):
        test_mca_csv_import_pipeline()

    def test_gleif_streaming(self):
        test_gleif_level1_and_level2_streaming()

    def test_reimport(self):
        test_idempotent_reimport()

    def test_full_synthetic_structure(self):
        test_full_synthetic_dataset_directory_structure()

    def test_health_check(self):
        test_health_check_lightweight()


