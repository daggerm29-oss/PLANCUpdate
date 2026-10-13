"""OpenSanctions connector for FollowTheMoney (FtM) bulk exports.

index:  https://data.opensanctions.org/datasets/latest/<dataset>/index.json (redirects to a versioned artifact)
data:   resources[name=entities.ftm.json] (JSON lines, sha1 checksum published)
Licence CC BY-NC 4.0 (non-commercial use with attribution). No key needed for bulk files.

The file is streamed three times so memory stays flat regardless of size:
  pass 1 side records (Sanction, Address, Identification), pass 2 nodes, pass 3 link schemas.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterator

from .. import db
from ..http import HttpClient, sha1_file
from ..settings import settings

BASE = "https://data.opensanctions.org/datasets/latest"
SOURCE = "opensanctions"
BATCH = 1000
log = logging.getLogger(__name__)

NODE_KIND = {
    "Company": "company",
    "Organization": "organization",
    "LegalEntity": "organization",
    "PublicBody": "organization",
    "Person": "person",
    "Vessel": "asset",
    "Airplane": "asset",
}
EDGE_SCHEMAS = {
    "Ownership": ("owner", "asset", "owns"),
    "Directorship": ("director", "organization", "director_of"),
    "Employment": ("employee", "employer", "employed_by"),
    "Membership": ("member", "organization", "member_of"),
    "Family": ("person", "relative", "family_of"),
    "Associate": ("person", "associate", "associate_of"),
    "Representation": ("agent", "client", "represents"),
    "UnknownLink": ("subject", "object", "linked_to"),
    "Succession": ("predecessor", "successor", "succeeded_by"),
}
IDENTIFIER_PROPS = {
    "leiCode": "lei", "swiftBic": "bic", "registrationNumber": "registration_number", "innCode": "inn",
    "ogrnCode": "ogrn", "imoNumber": "imo", "taxNumber": "tax_number", "vatCode": "vat", "dunsCode": "duns",
    "uniqueEntityId": "uei", "okpoCode": "okpo", "idNumber": "id_number", "passportNumber": "passport",
}
# Datasets published by an official authority: their links are "documented"; others are "reported".
OFFICIAL_PREFIXES = ("us_ofac", "eu_fsf", "gb_hmt", "gb_fcdo", "un_sc", "ch_seco", "ca_dfatd", "au_dfat", "us_trade", "jp_mof", "fr_tresor", "be_fod")


def first(values: list | None, default=None):
    return values[0] if values else default


def iter_entities(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                log.warning("skipping malformed line %d in %s", line_no, path)


def entity_url(entity_id: str) -> str:
    return f"https://www.opensanctions.org/entities/{entity_id}/"


def ensure_download(dataset: str, client: HttpClient | None = None) -> tuple[Path, str]:
    client = client or HttpClient()
    index = client.get_json(f"{BASE}/{dataset}/index.json")
    version = index["version"]
    resource = next(r for r in index["resources"] if r["name"] == "entities.ftm.json")
    dest = settings.raw_dir / "opensanctions" / dataset / version / "entities.ftm.json"
    if dest.exists() and (not resource.get("checksum") or sha1_file(dest) == resource["checksum"]):
        return dest, version
    log.info("downloading %s (%s bytes)", resource["url"], resource.get("size"))
    client.download(resource["url"], dest, expected_sha1=resource.get("checksum"))
    return dest, version


def _already_loaded(conn: sqlite3.Connection, dataset: str, version: str) -> bool:
    for row in conn.execute(
        "SELECT params FROM ingest_runs WHERE source_id=? AND status='success' ORDER BY id DESC LIMIT 20", (SOURCE,)
    ):
        params = db.loads(row["params"], {})
        if params.get("dataset") == dataset and params.get("version") == version:
            return True
    return False


def load(
    conn: sqlite3.Connection,
    dataset: str = "us_ofac_sdn",
    *,
    file: Path | None = None,
    force: bool = False,
    client: HttpClient | None = None,
) -> dict:
    if file is not None:
        path = Path(file)
        version = "local:" + sha1_file(path)[:12]
    else:
        path, version = ensure_download(dataset, client)
    if not force and _already_loaded(conn, dataset, version):
        run_id = db.start_run(conn, SOURCE, {"dataset": dataset, "version": version})
        db.finish_run(conn, run_id, "skipped", {"reason": "version already loaded"})
        return {"run_id": run_id, "status": "skipped", "version": version}
    run_id = db.start_run(conn, SOURCE, {"dataset": dataset, "version": version, "file": str(path)})
    stats: Counter = Counter()
    status, error = "success", None
    official = dataset.startswith(OFFICIAL_PREFIXES)
    try:
        sanctions, addresses, extra_ids, touched = _pass_side_records(conn, path, run_id, stats)
        loaded = _pass_nodes(conn, path, run_id, stats, sanctions, addresses, extra_ids, touched)
        _pass_edges(conn, path, run_id, stats, loaded, official)
    except Exception as exc:
        status, error = "failed", f"{type(exc).__name__}: {exc}"[:500]
        raise
    finally:
        db.finish_run(conn, run_id, status, stats, error)
    return {"run_id": run_id, "status": status, "version": version, **stats}


def _pass_side_records(conn, path, run_id, stats):
    sanctions: dict[str, list[dict]] = defaultdict(list)
    addresses: dict[str, str] = {}
    extra_ids: dict[str, list[tuple[str, str]]] = defaultdict(list)
    touched: set[str] = set()
    pending = 0
    conn.execute("BEGIN")
    try:
        for ent in iter_entities(path):
            schema, props = ent.get("schema"), ent.get("properties") or {}
            if schema == "Sanction":
                raw_id, state = db.upsert_raw(conn, SOURCE, ent["id"], ent, entity_url(ent["id"]), run_id)
                stats[f"sanction_{state}"] += 1
                item = {
                    "id": ent["id"],
                    "raw_record_id": raw_id,
                    "authority": first(props.get("authority")),
                    "program": first(props.get("program")) or first(props.get("programId")),
                    "start_date": first(props.get("startDate")) or first(props.get("listingDate")),
                    "end_date": first(props.get("endDate")),
                    "reason": (first(props.get("reason")) or "")[:300] or None,
                    "source_url": first(props.get("sourceUrl")),
                }
                for target in props.get("entity") or []:
                    sanctions[target].append(item)
                    if state != "unchanged":
                        touched.add(target)
                pending += 1
            elif schema == "Address":
                full = first(props.get("full")) or ", ".join(
                    filter(None, [first(props.get("street")), first(props.get("city")), first(props.get("country"))])
                )
                if full:
                    addresses[ent["id"]] = full
            elif schema in {"Identification", "Passport"}:
                number = first(props.get("number"))
                for holder in props.get("holder") or []:
                    if number:
                        extra_ids[holder].append(("id_number", number))
            if pending >= BATCH:
                conn.execute("COMMIT")
                conn.execute("BEGIN")
                pending = 0
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return sanctions, addresses, extra_ids, touched


def _pass_nodes(conn, path, run_id, stats, sanctions, addresses, extra_ids, touched) -> set[str]:
    loaded: set[str] = set()
    pending = 0
    conn.execute("BEGIN")
    try:
        for ent in iter_entities(path):
            schema = ent.get("schema")
            kind = NODE_KIND.get(schema)
            if not kind:
                continue
            eid = f"os:{ent['id']}"
            url = entity_url(ent["id"])
            raw_id, state = db.upsert_raw(conn, SOURCE, ent["id"], ent, url, run_id)
            stats[state] += 1
            loaded.add(eid)
            if state == "unchanged" and ent["id"] not in touched and db.entity_exists(conn, eid):
                continue
            props = ent.get("properties") or {}
            names = [(n, "alias") for n in (props.get("name") or [])[1:]]
            names += [(n, "alias") for n in (props.get("alias") or []) + (props.get("weakAlias") or [])]
            names += [(n, "previous") for n in props.get("previousName") or []]
            address = first(props.get("address")) or next(
                (addresses[a] for a in props.get("addressEntity") or [] if a in addresses), None
            )
            identifiers = [(scheme, v) for prop, scheme in IDENTIFIER_PROPS.items() for v in props.get(prop) or []]
            identifiers += extra_ids.get(ent["id"], [])
            entity_sanctions = sanctions.get(ent["id"], [])
            name = ent.get("caption") or first(props.get("name")) or ent["id"]
            db.upsert_entity(
                conn,
                {
                    "id": eid,
                    "source_id": SOURCE,
                    "kind": kind,
                    "name": name,
                    "country": first(props.get("country")) or first(props.get("jurisdiction"))
                    or first(props.get("nationality")) or first(props.get("citizenship")),
                    "jurisdiction": first(props.get("jurisdiction")),
                    "status": first(props.get("status")),
                    "legal_form": first(props.get("legalForm")),
                    "incorporation_date": first(props.get("incorporationDate")) or first(props.get("birthDate")),
                    "dissolution_date": first(props.get("dissolutionDate")) or first(props.get("deathDate")),
                    "address": address,
                    "topics": props.get("topics") or [],
                    "names": names,
                    "identifiers": identifiers,
                    "raw_record_id": raw_id,
                    "attrs": {
                        "schema": schema,
                        "datasets": ent.get("datasets"),
                        "referents": (ent.get("referents") or [])[:20],
                        "first_seen": ent.get("first_seen"),
                        "last_seen": ent.get("last_seen"),
                        "last_change": ent.get("last_change"),
                        "target": ent.get("target"),
                        "sanctions": [{k: v for k, v in s.items() if k != "raw_record_id"} for s in entity_sanctions],
                        "programs": props.get("programId"),
                        "notes": (first(props.get("notes")) or "")[:500] or None,
                        "source_urls": (props.get("sourceUrl") or [])[:5],
                        "positions": (props.get("position") or [])[:5],
                    },
                },
            )
            topics = ", ".join(props.get("topics") or []) or "none"
            db.add_evidence(
                conn,
                key=f"os:entity:{ent['id']}",
                source_id=SOURCE,
                url=url,
                title=f"OpenSanctions: {name}",
                excerpt=f"{schema} in dataset(s) {', '.join(ent.get('datasets') or [])}; topics: {topics}",
                raw_record_id=raw_id,
                entity_id=eid,
            )
            for s in entity_sanctions:
                db.add_evidence(
                    conn,
                    key=f"os:sanction:{s['id']}:{ent['id']}",
                    source_id=SOURCE,
                    url=s.get("source_url") or url,
                    title=f"Sanction listing: {s.get('authority') or 'unknown authority'}",
                    excerpt=(
                        f"Program {s.get('program') or 'n/a'}; listed {s.get('start_date') or 'n/a'}"
                        + (f"; reason: {s['reason']}" if s.get("reason") else "")
                    ),
                    raw_record_id=s["raw_record_id"],
                    entity_id=eid,
                )
            pending += 1
            if pending >= BATCH:
                conn.execute("COMMIT")
                conn.execute("BEGIN")
                pending = 0
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return loaded


def _pass_edges(conn, path, run_id, stats, loaded: set[str], official: bool) -> None:
    pending = 0
    conn.execute("BEGIN")
    try:
        for ent in iter_entities(path):
            spec = EDGE_SCHEMAS.get(ent.get("schema"))
            if not spec:
                continue
            subj_prop, obj_prop, predicate = spec
            props = ent.get("properties") or {}
            raw_id, state = db.upsert_raw(conn, SOURCE, ent["id"], ent, entity_url(ent["id"]), run_id)
            stats[f"link_{state}"] += 1
            for s in props.get(subj_prop) or []:
                for o in props.get(obj_prop) or []:
                    subject, obj = f"os:{s}", f"os:{o}"
                    if subject not in loaded or obj not in loaded or subject == obj:
                        stats["link_skipped_missing_endpoint"] += 1
                        continue
                    rel_id = db.upsert_relationship(
                        conn,
                        {
                            "source_id": SOURCE,
                            "subject_id": subject,
                            "predicate": predicate,
                            "object_id": obj,
                            "basis": "documented" if official else "reported",
                            "confidence": 0.95 if official else 0.7,
                            "start_date": first(props.get("startDate")),
                            "end_date": first(props.get("endDate")),
                            "attrs": {
                                "schema": ent.get("schema"),
                                "ftm_id": ent["id"],
                                "role": first(props.get("role")),
                                "relationship": first(props.get("relationship")),
                                "percentage": first(props.get("percentage")),
                                "summary": (first(props.get("summary")) or "")[:300] or None,
                                "datasets": ent.get("datasets"),
                            },
                            "raw_record_id": raw_id,
                        },
                    )
                    detail = first(props.get("role")) or first(props.get("relationship")) or first(props.get("percentage"))
                    db.add_evidence(
                        conn,
                        key=f"os:link:{ent['id']}:{s}:{o}",
                        source_id=SOURCE,
                        url=first(props.get("sourceUrl")) or entity_url(s),
                        title=f"OpenSanctions {ent.get('schema')} record",
                        excerpt=(
                            f"{ent.get('schema')}: {s} -> {o}"
                            + (f" ({detail})" if detail else "")
                            + f"; dataset(s): {', '.join(ent.get('datasets') or [])}"
                        ),
                        raw_record_id=raw_id,
                        relationship_id=rel_id,
                    )
                    stats["links"] += 1
            pending += 1
            if pending >= BATCH:
                conn.execute("COMMIT")
                conn.execute("BEGIN")
                pending = 0
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
