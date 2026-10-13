"""GLEIF Golden Copy / concatenated files: Level 1 (LEI-CDF) and Level 2 (RR-CDF) XML, streamed.

Same entity ids, predicates and evidence keys as the API connector (gleif.py), so file and API loads merge.
Level 2 direction is preserved exactly: StartNode = child, EndNode = parent
  child  --direct_parent-->   parent   (IS_DIRECTLY_CONSOLIDATED_BY)
  child  --ultimate_parent--> parent   (IS_ULTIMATELY_CONSOLIDATED_BY)
A parent that is not in the loaded Level 1 data becomes a stub entity (attrs.stub = true) so the edge is kept;
loading its Level 1 record later replaces the stub.
"""
from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator

from .. import db
from ..resolution.identifiers import CIN_RE
from ..resolution.normalize import normalize_identifier
from .files import batched, file_provenance, ingest_run
from .gleif import API, PREDICATE_BY_TYPE, SOURCE

INDIA_MCA_RA = "RA000394"  # GLEIF registration-authority code for India's Ministry of Corporate Affairs
METHOD_L1 = "bulk_file:gleif_lei_cdf_xml"
METHOD_L2 = "bulk_file:gleif_rr_cdf_xml"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.find("/".join(f"{{*}}{p}" for p in path.split("/")))
    return found.text.strip() if found is not None and found.text and found.text.strip() else None


def _date(value: str | None) -> str | None:
    return value[:10] if value else None


def iter_records(path: Path, record_tag: str) -> Iterator[ET.Element]:
    for _, elem in ET.iterparse(path, events=("end",)):
        if _local(elem.tag) == record_tag:
            yield elem
            elem.clear()


def _address(addr: ET.Element | None) -> str | None:
    if addr is None:
        return None
    parts = [_text(addr, "FirstAddressLine")]
    parts += [(a.text or "").strip() for a in addr.findall("{*}AdditionalAddressLine")]
    parts += [_text(addr, "City"), " ".join(p for p in (_text(addr, "Region"), _text(addr, "PostalCode")) if p),
              _text(addr, "Country")]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def parse_level1(rec: ET.Element) -> dict:
    lei = _text(rec, "LEI")
    ent = rec.find("{*}Entity")
    reg = rec.find("{*}Registration")
    if not lei or ent is None:
        raise ValueError("LEI record without LEI/Entity")
    country = _text(ent, "LegalAddress/Country")
    names: list[tuple[str, str]] = []
    for other in ent.findall("{*}OtherEntityNames/{*}OtherEntityName"):
        if other.text and other.text.strip():
            names.append((other.text.strip(), "previous" if other.get("type") == "PREVIOUS_LEGAL_NAME" else "alias"))
    for other in ent.findall("{*}TransliteratedOtherEntityNames/{*}TransliteratedOtherEntityName"):
        if other.text and other.text.strip():
            names.append((other.text.strip(), "transliteration"))
    ra_id = _text(ent, "RegistrationAuthority/RegistrationAuthorityID")
    ra_entity = _text(ent, "RegistrationAuthority/RegistrationAuthorityEntityID")
    identifiers: list[tuple[str, str]] = [("lei", lei)]
    if ra_entity:
        identifiers.append(("registration_number", ra_entity))
        compact = normalize_identifier("cin", ra_entity)
        if CIN_RE.match(compact) and (ra_id == INDIA_MCA_RA or (country or "").upper() == "IN"):
            identifiers.append(("cin", compact))
    category = _text(ent, "EntityCategory")
    return {
        "id": f"gleif:{lei}",
        "source_id": SOURCE,
        "kind": "organization" if category in {"RESIDENT_GOVERNMENT_ENTITY", "INTERNATIONAL_ORGANIZATION"} else "company",
        "name": _text(ent, "LegalName") or lei,
        "country": country,
        "jurisdiction": _text(ent, "LegalJurisdiction"),
        "status": (_text(ent, "EntityStatus") or "").lower() or None,
        "legal_form": _text(ent, "LegalForm/EntityLegalFormCode") or _text(ent, "LegalForm/OtherLegalForm"),
        "incorporation_date": _date(_text(ent, "EntityCreationDate")),
        "dissolution_date": _date(_text(ent, "EntityExpirationDate")),
        "address": _address(ent.find("{*}LegalAddress")),
        "topics": [],
        "names": names,
        "identifiers": identifiers,
        "attrs": {
            "lei": lei,
            "category": category,
            "other_legal_form": _text(ent, "LegalForm/OtherLegalForm"),
            "registration_authority": ra_id,
            "registration_status": _text(reg, "RegistrationStatus"),
            "initial_registration_date": _date(_text(reg, "InitialRegistrationDate")),
            "last_update_date": _date(_text(reg, "LastUpdateDate")),
            "next_renewal_date": _date(_text(reg, "NextRenewalDate")),
            "corroboration_level": _text(reg, "ValidationSources"),
            "managing_lou": _text(reg, "ManagingLOU"),
            "headquarters_address": _address(ent.find("{*}HeadquartersAddress")),
            "successor_lei": _text(ent, "SuccessorEntity/SuccessorLEI"),
        },
    }


def load_level1(conn: sqlite3.Connection, path: Path) -> dict:
    path = Path(path)
    prov = file_provenance(path)
    with ingest_run(conn, SOURCE, {"kind": "gleif_level1_xml", **prov}) as (run_id, stats):
        def handle(rec: ET.Element) -> None:
            entity = parse_level1(rec)
            lei = entity["attrs"]["lei"]
            url = f"{API}/lei-records/{lei}"
            payload = ET.tostring(rec, encoding="unicode")
            raw_id, state = db.upsert_raw(conn, SOURCE, f"lei-xml:{lei}", payload, url, run_id, "application/xml")
            stats[state] += 1
            existing = conn.execute("SELECT attrs FROM entities WHERE id=?", (entity["id"],)).fetchone()
            if state == "unchanged" and existing and not db.loads(existing[0], {}).get("stub"):
                return
            entity["raw_record_id"] = raw_id
            db.upsert_entity(conn, entity)
            db.add_evidence(
                conn,
                key=f"gleif:lei:{lei}",
                source_id=SOURCE,
                url=url,
                title=f"GLEIF LEI record {lei} ({prov['file']})",
                excerpt=(f"{entity['name']}; legal address: {entity['address'] or 'n/a'}; entity status: "
                         f"{entity['status'] or 'n/a'}; LEI registration: {entity['attrs']['registration_status'] or 'n/a'}"),
                raw_record_id=raw_id,
                entity_id=entity["id"],
                extraction_method=METHOD_L1,
                confidence=1.0,
            )

        batched(conn, iter_records(path, "LEIRecord"), handle, stats)
    return {"run_id": run_id, "file": prov["file"], **stats}


def _ensure_stub(conn: sqlite3.Connection, lei: str, stats) -> None:
    if db.entity_exists(conn, f"gleif:{lei}"):
        return
    db.upsert_entity(conn, {
        "id": f"gleif:{lei}", "source_id": SOURCE, "kind": "company", "name": f"LEI {lei} (record not loaded)",
        "identifiers": [("lei", lei)], "attrs": {"lei": lei, "stub": True},
    })
    stats["stub_entities"] += 1


def load_level2(conn: sqlite3.Connection, path: Path) -> dict:
    path = Path(path)
    prov = file_provenance(path)
    with ingest_run(conn, SOURCE, {"kind": "gleif_level2_xml", **prov}) as (run_id, stats):
        def handle(rec: ET.Element) -> None:
            rel = rec.find("{*}Relationship")
            reg = rec.find("{*}Registration")
            rtype = _text(rel, "RelationshipType")
            predicate = PREDICATE_BY_TYPE.get(rtype or "")
            if not predicate:
                stats["skipped_type"] += 1
                return
            if _text(rel, "StartNode/NodeIDType") != "LEI" or _text(rel, "EndNode/NodeIDType") != "LEI":
                stats["skipped_non_lei_node"] += 1
                return
            child, parent = _text(rel, "StartNode/NodeID"), _text(rel, "EndNode/NodeID")
            if not child or not parent or child == parent:
                raise ValueError("relationship without two distinct LEIs")
            kind = "direct" if predicate == "direct_parent" else "ultimate"
            url = f"{API}/lei-records/{child}/{kind}-parent-relationship"
            payload = ET.tostring(rec, encoding="unicode")
            raw_id, state = db.upsert_raw(conn, SOURCE, f"rel-xml:{child}:{rtype}:{parent}", payload, url, run_id,
                                          "application/xml")
            stats[f"relationship_{state}"] += 1
            _ensure_stub(conn, child, stats)
            _ensure_stub(conn, parent, stats)
            periods = [{"start": _date(_text(p, "StartDate")), "end": _date(_text(p, "EndDate")), "type": _text(p, "PeriodType")}
                       for p in rel.findall("{*}RelationshipPeriods/{*}RelationshipPeriod")]
            period = next((p for p in periods if p["type"] == "RELATIONSHIP_PERIOD"), {})
            quantifiers = [{"method": _text(q, "MeasurementMethod"), "amount": _text(q, "QuantifierAmount"),
                            "units": _text(q, "QuantifierUnits")}
                           for q in rel.findall("{*}RelationshipQuantifiers/{*}RelationshipQuantifier")]
            rel_id = db.upsert_relationship(conn, {
                "source_id": SOURCE,
                "subject_id": f"gleif:{child}",
                "predicate": predicate,
                "object_id": f"gleif:{parent}",
                "basis": "documented",
                "confidence": 1.0,
                "start_date": period.get("start"),
                "end_date": period.get("end"),
                "status": (_text(rel, "RelationshipStatus") or "").lower() or None,
                "attrs": {
                    "gleif_type": rtype,
                    "registration_status": _text(reg, "RegistrationStatus"),
                    "corroboration_level": _text(reg, "ValidationSources"),
                    "corroboration_documents": _text(reg, "ValidationDocuments"),
                    "validation_reference": _text(reg, "ValidationReference"),
                    "periods": periods,
                    "quantifiers": quantifiers,
                    "accounting_standard": _text(rel, "RelationshipQualifiers/RelationshipQualifier/QualifierCategory"),
                },
                "raw_record_id": raw_id,
            })
            pct = next((q["amount"] for q in quantifiers if q["units"] == "PERCENTAGE"), None)
            db.add_evidence(
                conn,
                key=f"gleif:rel:{child}:{predicate}:{parent}",
                source_id=SOURCE,
                url=url,
                title=f"GLEIF Level 2 relationship record ({prov['file']})",
                excerpt=(f"{child} {rtype} {parent} (child -> parent); status {_text(rel, 'RelationshipStatus')}; "
                         f"corroboration {_text(reg, 'ValidationSources')}; period start {period.get('start') or 'n/a'}"
                         + (f"; consolidation {pct}%" if pct else "")),
                raw_record_id=raw_id,
                relationship_id=rel_id,
                extraction_method=METHOD_L2,
                confidence=1.0,
            )
            stats["relationships"] += 1

        batched(conn, iter_records(path, "RelationshipRecord"), handle, stats)
    return {"run_id": run_id, "file": prov["file"], **stats}
