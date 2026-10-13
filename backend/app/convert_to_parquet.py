"""Local high-performance batch converter for PLANC datasets to Parquet.

Converts full corporate intelligence datasets (MCA, GLEIF XML, ICIJ, NSE, OpenSanctions)
into sorted, partitioned, and indexed Parquet files ready for private Hugging Face hosting
and serverless DuckDB querying from Vercel.

Usage:
  # Run from the backend directory:
  python -m app.convert_to_parquet --dir "C:\\Users\\Akhil S Kumar\\OneDrive\\Documents\\recent\\planc (1)\\datasets" --out "./parquet_export"

  # Or convert without GLEIF to test first:
  python -m app.convert_to_parquet --dir "..." --out "./parquet_export" --skip-gleif
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Generator

try:
    import duckdb
    DUCKDB_AVAILABLE = True
except ImportError:
    DUCKDB_AVAILABLE = False


def normalize_name(name: str) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", (name or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_identifier(scheme: str, value: str) -> str:
    v = (value or "").strip().upper()
    return re.sub(r"[\s\-_/]", "", v)


class ParquetConverter:
    def __init__(self, output_dir: Path, batch_size: int = 50000) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.batch_size = batch_size

        if not DUCKDB_AVAILABLE:
            raise RuntimeError(
                "duckdb Python package is required for Parquet conversion.\n"
                "Install it with: pip install duckdb"
            )

        # Temporary local database used to stage, sort, and export Parquet files
        self.temp_db_path = self.output_dir / "_staging.duckdb"
        if self.temp_db_path.exists():
            self.temp_db_path.unlink()

        self.con = duckdb.connect(str(self.temp_db_path))
        self._init_staging_tables()

    def _init_staging_tables(self) -> None:
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                id VARCHAR PRIMARY KEY,
                source_id VARCHAR NOT NULL,
                kind VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                name_norm VARCHAR NOT NULL,
                country VARCHAR,
                jurisdiction VARCHAR,
                status VARCHAR,
                legal_form VARCHAR,
                incorporation_date VARCHAR,
                address VARCHAR,
                topics VARCHAR,
                attrs VARCHAR,
                first_seen_at VARCHAR,
                updated_at VARCHAR
            );

            CREATE TABLE IF NOT EXISTS identifiers (
                entity_id VARCHAR NOT NULL,
                scheme VARCHAR NOT NULL,
                value VARCHAR NOT NULL,
                PRIMARY KEY (entity_id, scheme, value)
            );

            CREATE TABLE IF NOT EXISTS relationships (
                id BIGINT PRIMARY KEY,
                source_id VARCHAR NOT NULL,
                subject_id VARCHAR NOT NULL,
                predicate VARCHAR NOT NULL,
                object_id VARCHAR NOT NULL,
                basis VARCHAR,
                confidence DOUBLE,
                start_date VARCHAR,
                end_date VARCHAR,
                status VARCHAR,
                attrs VARCHAR
            );
        """)

    def insert_entities_batch(self, entities: list[tuple]) -> None:
        if not entities:
            return
        self.con.executemany("""
            INSERT OR REPLACE INTO entities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, entities)

    def insert_identifiers_batch(self, identifiers: list[tuple]) -> None:
        if not identifiers:
            return
        self.con.executemany("""
            INSERT OR IGNORE INTO identifiers VALUES (?, ?, ?)
        """, identifiers)

    def insert_relationships_batch(self, rels: list[tuple]) -> None:
        if not rels:
            return
        self.con.executemany("""
            INSERT OR REPLACE INTO relationships VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rels)

    def process_mca(self, mca_file: Path) -> int:
        print(f"\n[PROCESSING MCA] {mca_file.name}...")
        t0 = time.time()
        entities = []
        identifiers = []
        count = 0

        with open(mca_file, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cin = row.get("CIN") or row.get("cin") or ""
                name = row.get("Company Name") or row.get("company_name") or ""
                if not cin or not name:
                    continue

                clean_cin = normalize_identifier("cin", cin)
                ent_id = f"in:mca:{clean_cin}"
                name_norm = normalize_name(name)
                state = row.get("Company State", "")
                status = row.get("Company Status", "Active")
                inc_date = row.get("Company Registration Date", "")
                address = row.get("Company Address", "")

                entities.append((
                    ent_id, "mca", "company", name, name_norm, "in", state, status,
                    row.get("Company Class", ""), inc_date, address, "corporate_register",
                    json.dumps({"cin": clean_cin, "roc": row.get("Company ROC", "")}),
                    "2026-10-12T00:00:00Z", "2026-10-12T00:00:00Z"
                ))
                identifiers.append((ent_id, "cin", clean_cin))

                count += 1
                if len(entities) >= self.batch_size:
                    self.insert_entities_batch(entities)
                    self.insert_identifiers_batch(identifiers)
                    entities.clear()
                    identifiers.clear()
                    print(f"  Processed {count:,} MCA records...")

            if entities:
                self.insert_entities_batch(entities)
                self.insert_identifiers_batch(identifiers)

        print(f"  [DONE] {mca_file.name}: {count:,} companies in {time.time() - t0:.1f}s")
        return count

    def process_gleif_level1(self, xml_source: Path) -> int:
        print(f"\n[PROCESSING GLEIF LEVEL 1] {xml_source.name} (streaming XML)...")
        t0 = time.time()
        count = 0
        entities = []
        identifiers = []

        # Support direct XML or reading inside ZIP
        file_obj = None
        if xml_source.suffix.lower() == ".zip":
            zf = zipfile.ZipFile(xml_source, "r")
            xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            if not xml_names:
                print("  [WARN] No XML found in GLEIF ZIP")
                return 0
            file_obj = zf.open(xml_names[0], "r")
        else:
            file_obj = open(xml_source, "rb")

        try:
            # Memory-safe iterparse: clearing root avoids 8GB memory leak
            context = ET.iterparse(file_obj, events=("start", "end"))
            root = None

            for event, elem in context:
                if event == "start" and root is None:
                    root = elem

                if event == "end" and elem.tag.endswith("LEIRecord"):
                    lei_elem = elem.find(".//{*}LEI")
                    name_elem = elem.find(".//{*}LegalName")

                    if lei_elem is not None and lei_elem.text and name_elem is not None and name_elem.text:
                        lei = normalize_identifier("lei", lei_elem.text)
                        name = name_elem.text.strip()
                        ent_id = f"lei:{lei}"
                        name_norm = normalize_name(name)

                        country_elem = elem.find(".//{*}LegalAddress/{*}Country")
                        country = country_elem.text.lower() if country_elem is not None and country_elem.text else "unknown"

                        status_elem = elem.find(".//{*}EntityStatus")
                        status = status_elem.text if status_elem is not None and status_elem.text else "ACTIVE"

                        entities.append((
                            ent_id, "gleif", "company", name, name_norm, country, None,
                            status, "Legal Entity", None, None, "gleif_lei",
                            json.dumps({"lei": lei}), "2026-10-12T00:00:00Z", "2026-10-12T00:00:00Z"
                        ))
                        identifiers.append((ent_id, "lei", lei))
                        count += 1

                        if len(entities) >= self.batch_size:
                            self.insert_entities_batch(entities)
                            self.insert_identifiers_batch(identifiers)
                            entities.clear()
                            identifiers.clear()
                            print(f"  Processed {count:,} GLEIF Level 1 LEIs...")

                    # Memory hygiene
                    elem.clear()
                    if root is not None:
                        root.clear()

            if entities:
                self.insert_entities_batch(entities)
                self.insert_identifiers_batch(identifiers)

        finally:
            file_obj.close()

        print(f"  [DONE] GLEIF Level 1: {count:,} LEIs in {time.time() - t0:.1f}s")
        return count

    def process_gleif_level2(self, xml_source: Path) -> int:
        print(f"\n[PROCESSING GLEIF LEVEL 2] {xml_source.name} (relationships)...")
        t0 = time.time()
        count = 0
        rels = []

        file_obj = None
        if xml_source.suffix.lower() == ".zip":
            zf = zipfile.ZipFile(xml_source, "r")
            xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            if not xml_names:
                return 0
            file_obj = zf.open(xml_names[0], "r")
        else:
            file_obj = open(xml_source, "rb")

        try:
            context = ET.iterparse(file_obj, events=("start", "end"))
            root = None
            rel_id = int(time.time() * 1000)

            for event, elem in context:
                if event == "start" and root is None:
                    root = elem

                if event == "end" and elem.tag.endswith("RelationshipRecord"):
                    sub_elem = elem.find(".//{*}StartNode/{*}NodeID")
                    obj_elem = elem.find(".//{*}EndNode/{*}NodeID")
                    type_elem = elem.find(".//{*}RelationshipType")

                    if sub_elem is not None and sub_elem.text and obj_elem is not None and obj_elem.text:
                        sub_lei = normalize_identifier("lei", sub_elem.text)
                        obj_lei = normalize_identifier("lei", obj_elem.text)
                        predicate = "direct_parent" if (type_elem is not None and "DIRECT" in (type_elem.text or "")) else "ultimate_parent"

                        rels.append((
                            rel_id, "gleif", f"lei:{sub_lei}", predicate, f"lei:{obj_lei}",
                            "GLEIF Level 2 RR XML", 0.99, None, None, "Active", "{}"
                        ))
                        rel_id += 1
                        count += 1

                        if len(rels) >= self.batch_size:
                            self.insert_relationships_batch(rels)
                            rels.clear()
                            print(f"  Processed {count:,} GLEIF relationships...")

                    elem.clear()
                    if root is not None:
                        root.clear()

            if rels:
                self.insert_relationships_batch(rels)

        finally:
            file_obj.close()

        print(f"  [DONE] GLEIF Level 2: {count:,} relationships in {time.time() - t0:.1f}s")
        return count

    def process_opensanctions(self, csv_file: Path) -> int:
        print(f"\n[PROCESSING OPENSANCTIONS] {csv_file.name}...")
        t0 = time.time()
        count = 0
        entities = []
        identifiers = []

        with open(csv_file, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                os_id = row.get("id") or ""
                name = row.get("name") or row.get("caption") or ""
                if not os_id or not name:
                    continue

                ent_id = f"sanction:opensanctions:{os_id}"
                name_norm = normalize_name(name)
                schema = row.get("schema", "Company").lower()
                kind = "person" if schema in ("person", "individual") else "company"
                country = (row.get("country") or "unknown").lower()

                entities.append((
                    ent_id, "opensanctions", kind, name, name_norm, country, None,
                    "Sanctioned", schema, row.get("birth_date"), None,
                    "sanctions,watchlist",
                    json.dumps({"datasets": row.get("dataset", ""), "target": row.get("target")}),
                    "2026-10-12T00:00:00Z", "2026-10-12T00:00:00Z"
                ))
                count += 1

                if len(entities) >= self.batch_size:
                    self.insert_entities_batch(entities)
                    entities.clear()
                    print(f"  Processed {count:,} OpenSanctions entities...")

            if entities:
                self.insert_entities_batch(entities)

        print(f"  [DONE] OpenSanctions: {count:,} entities in {time.time() - t0:.1f}s")
        return count

    def export_final_parquets(self) -> dict[str, int]:
        print("\n=======================================================")
        print("OPTIMIZING & EXPORTING FINAL PARQUET FILES")
        print("=======================================================")

        # 1. Export entities.parquet sorted by name_norm
        entities_out = self.output_dir / "entities.parquet"
        print(f"Sorting and exporting entities to: {entities_out.name}...")
        self.con.execute(f"""
            COPY (
                SELECT * FROM entities ORDER BY name_norm
            ) TO '{str(entities_out).replace('\\', '/')}' (
                FORMAT PARQUET,
                COMPRESSION ZSTD,
                ROW_GROUP_SIZE 100000
            );
        """)

        # 2. Export identifiers.parquet sorted by value (enables instant binary search pushdown)
        identifiers_out = self.output_dir / "identifiers.parquet"
        print(f"Sorting and exporting identifiers to: {identifiers_out.name}...")
        self.con.execute(f"""
            COPY (
                SELECT * FROM identifiers ORDER BY value
            ) TO '{str(identifiers_out).replace('\\', '/')}' (
                FORMAT PARQUET,
                COMPRESSION ZSTD,
                ROW_GROUP_SIZE 100000
            );
        """)

        # 3. Export relationships.parquet
        relationships_out = self.output_dir / "relationships.parquet"
        print(f"Exporting relationships to: {relationships_out.name}...")
        self.con.execute(f"""
            COPY (
                SELECT * FROM relationships ORDER BY id
            ) TO '{str(relationships_out).replace('\\', '/')}' (
                FORMAT PARQUET,
                COMPRESSION ZSTD,
                ROW_GROUP_SIZE 100000
            );
        """)

        ent_cnt = self.con.execute("SELECT count(*) FROM entities").fetchone()[0]
        id_cnt = self.con.execute("SELECT count(*) FROM identifiers").fetchone()[0]
        rel_cnt = self.con.execute("SELECT count(*) FROM relationships").fetchone()[0]

        self.con.close()
        if self.temp_db_path.exists():
            self.temp_db_path.unlink()

        print("\n[SUCCESS] Conversion Complete:")
        print(f"  - {entities_out.name}: {ent_cnt:,} entities ({entities_out.stat().st_size / (1024*1024):.2f} MB)")
        print(f"  - {identifiers_out.name}: {id_cnt:,} identifiers ({identifiers_out.stat().st_size / (1024*1024):.2f} MB)")
        print(f"  - {relationships_out.name}: {rel_cnt:,} relationships ({relationships_out.stat().st_size / (1024*1024):.2f} MB)")

        return {"entities": ent_cnt, "identifiers": id_cnt, "relationships": rel_cnt}


def main():
    parser = argparse.ArgumentParser(description="Convert PLANC datasets to Hugging Face Parquet format")
    parser.add_argument("--dir", type=Path, required=True, help="Path to local datasets directory")
    parser.add_argument("--out", type=Path, default=Path("./parquet_export"), help="Output directory for Parquet files")
    parser.add_argument("--batch-size", type=int, default=50000, help="Batch insertion size")
    parser.add_argument("--skip-gleif", action="store_true", help="Skip heavy GLEIF XML archives")
    args = parser.parse_args()

    root = args.dir.resolve()
    if not root.exists():
        print(f"Error: Datasets directory '{root}' does not exist.")
        sys.exit(1)

    print(f"Starting PLANC Parquet conversion from: {root}")
    converter = ParquetConverter(output_dir=args.out.resolve(), batch_size=args.batch_size)

    # 1. Discover and process MCA
    for mca_path in root.glob("mca/*.csv"):
        converter.process_mca(mca_path)

    # 2. Discover and process OpenSanctions
    for os_path in root.glob("opensanctions/*.csv"):
        converter.process_opensanctions(os_path)

    # 3. Discover and process GLEIF
    if not args.skip_gleif:
        # Level 1
        for g1 in list(root.glob("gleif/level1.zip")) + list(root.glob("gleif/level1/*.xml")):
            converter.process_gleif_level1(g1)
        # Level 2
        for g2 in list(root.glob("gleif/level2.zip")) + list(root.glob("gleif/level2/*.xml")):
            converter.process_gleif_level2(g2)
    else:
        print("\n[SKIPPING] GLEIF Level 1 and Level 2 (--skip-gleif specified)")

    # 4. Final export
    converter.export_final_parquets()

    print("\n-------------------------------------------------------")
    print("NEXT STEPS: UPLOAD TO PRIVATE HUGGING FACE DATASET")
    print("-------------------------------------------------------")
    print("1. Install huggingface-cli:")
    print("   pip install huggingface_hub")
    print("\n2. Log in with your Hugging Face Write Token:")
    print("   huggingface-cli login")
    print("\n3. Create your private dataset repo on Hugging Face (e.g. your_user/planc-datasets)")
    print("\n4. Upload the generated Parquet files:")
    print(f'   huggingface-cli upload your_user/planc-datasets "{args.out.resolve()}" . --repo-type dataset')
    print("\n5. In Vercel Environment Variables, set:")
    print("   HF_TOKEN=hf_your_read_token")
    print("   HF_DATASET_REPO=your_user/planc-datasets")
    print("   ENABLE_DUCKDB_HF=true")
    print("-------------------------------------------------------")


if __name__ == "__main__":
    main()
