"""Entity resolution: exact identifiers first, fuzzy names second, never destructive.

Auto/confirmed matches become `same_as` edges (source `resolver`, basis `inferred`)
that an analyst can reject at any time; fuzzy name matches only enter the review queue.
"""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict

from rapidfuzz import fuzz

from .. import db
from .normalize import normalize_name, search_tokens

RESOLVER = "resolver"
# Nationally unique identifiers. CIN/LLPIN/FCRN (MCA), PAN/GSTIN (Income Tax / GST), DIN (MCA director id).
AUTO_SCHEMES = {"lei", "bic", "imo", "ogrn", "inn", "duns", "uei", "cin", "llpin", "fcrn", "pan", "gstin", "din"}
COUNTRY_SCOPED = {"registration_number", "tax_number", "vat", "okpo"}
FUZZY_THRESHOLD = 90.0


def name_similarity(a: str, b: str) -> float:
    """0-100. token_sort penalises extra tokens, so 'sberbank leasing' does not match 'sberbank'."""
    if not a or not b:
        return 0.0
    return 0.7 * fuzz.token_sort_ratio(a, b) + 0.3 * fuzz.ratio(a, b)


def best_name_score(a_names: list[str], b_names: list[str]) -> tuple[float, str, str]:
    best = (0.0, "", "")
    for a in a_names:
        for b in b_names:
            score = name_similarity(a, b)
            if score > best[0]:
                best = (score, a, b)
    return best


def adjusted_score(score: float, country_a: str | None, country_b: str | None) -> float:
    if country_a and country_b:
        score += 2.0 if country_a.lower() == country_b.lower() else -15.0
    return max(0.0, min(100.0, score))


def is_name_match(a: str, b: str, country_a: str | None = None, country_b: str | None = None,
                  threshold: float = FUZZY_THRESHOLD) -> bool:
    return adjusted_score(name_similarity(normalize_name(a), normalize_name(b)), country_a, country_b) >= threshold


def _names(conn: sqlite3.Connection, entity_id: str) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT name_norm FROM entity_names WHERE entity_id=?", (entity_id,))]


def upsert_candidate(conn, left: str, right: str, method: str, score: float, features: dict, status: str) -> int:
    if left > right:
        left, right = right, left
    row = conn.execute(
        """
        INSERT INTO match_candidates(left_id, right_id, method, score, features, status, created_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(left_id, right_id) DO UPDATE SET
            method = CASE WHEN excluded.method = 'identifier' THEN 'identifier' ELSE match_candidates.method END,
            score = MAX(match_candidates.score, excluded.score),
            features = CASE WHEN excluded.method = 'identifier' OR match_candidates.method != 'identifier'
                            THEN excluded.features ELSE match_candidates.features END,
            status = CASE WHEN match_candidates.status IN ('confirmed', 'rejected') THEN match_candidates.status
                          WHEN excluded.status = 'auto' THEN 'auto' ELSE match_candidates.status END
        RETURNING id
        """,
        (left, right, method, score, db.dumps(features), status, db.utcnow()),
    ).fetchone()
    return int(row[0])


def describe_match(match: sqlite3.Row | dict, features: dict) -> str:
    if match["method"] == "identifier":
        ids = ", ".join(f"{i['scheme'].upper()}={i['value']}" for i in features.get("identifiers", []))
        text = f"Records share identifier(s) {ids}"
    else:
        text = (
            f"Name similarity {features.get('adjusted_score')} between '{features.get('left_name')}' and "
            f"'{features.get('right_name')}'"
        )
    if match["status"] == "confirmed":
        text += "; confirmed by analyst"
    return text + ". Algorithmic match, not an official determination."


def sync_same_as(conn: sqlite3.Connection, match_id: int) -> None:
    m = conn.execute("SELECT * FROM match_candidates WHERE id=?", (match_id,)).fetchone()
    if m is None:
        return
    if m["status"] in ("auto", "confirmed"):
        features = db.loads(m["features"], {})
        rel_id = db.upsert_relationship(
            conn,
            {
                "source_id": RESOLVER,
                "subject_id": m["left_id"],
                "predicate": "same_as",
                "object_id": m["right_id"],
                "basis": "inferred",
                "confidence": round(float(m["score"]), 3),
                "status": m["status"],
                "attrs": {"match_id": m["id"], "method": m["method"], "features": features},
            },
        )
        db.add_evidence(
            conn,
            key=f"match:{m['id']}",
            source_id=RESOLVER,
            url=None,
            title=f"Entity resolution ({m['method']}, {m['status']})",
            excerpt=describe_match(m, features) + (f" Note: {m['reviewer_note']}" if m["reviewer_note"] else ""),
            relationship_id=rel_id,
        )
    else:
        conn.execute(
            "DELETE FROM relationships WHERE source_id=? AND subject_id=? AND predicate='same_as' AND object_id=?",
            (RESOLVER, m["left_id"], m["right_id"]),
        )


def match_identifiers(conn: sqlite3.Connection) -> int:
    pairs: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for r in conn.execute(
        """
        SELECT a.entity_id AS l, b.entity_id AS r, a.scheme, a.value, ea.country AS lc, eb.country AS rc
        FROM identifiers a
        JOIN identifiers b ON a.scheme = b.scheme AND a.value = b.value AND a.entity_id < b.entity_id
        JOIN entities ea ON ea.id = a.entity_id
        JOIN entities eb ON eb.id = b.entity_id
        WHERE ea.source_id != eb.source_id
        """
    ):
        pairs[(r["l"], r["r"])].append(r)
    count = 0
    with db.transaction(conn):
        for (left, right), ids in pairs.items():
            auto = [i for i in ids if i["scheme"] in AUTO_SCHEMES]
            scoped = [i for i in ids if i["scheme"] in COUNTRY_SCOPED and i["lc"] and i["lc"] == i["rc"]]
            if not auto and not scoped:
                continue
            features = {"identifiers": [{"scheme": i["scheme"], "value": i["value"]} for i in auto + scoped]}
            mid = upsert_candidate(conn, left, right, "identifier", 0.99 if auto else 0.9, features,
                                   "auto" if auto else "pending")
            sync_same_as(conn, mid)
            count += 1
    return count


def match_names(conn: sqlite3.Connection, source: str, target: str, threshold: float = FUZZY_THRESHOLD,
                max_per_entity: int = 5) -> int:
    count = 0
    entities = conn.execute(
        "SELECT id, country FROM entities WHERE source_id=? AND kind IN ('company','organization')", (source,)
    ).fetchall()
    for ent in entities:
        a_names = _names(conn, ent["id"])
        tokens: list[str] = []
        for name in a_names:
            tokens += [t for t in search_tokens(name) if len(t) >= 3 and t not in tokens]
        if not tokens:
            continue
        query = " OR ".join(f'"{t}"' for t in tokens[:8])
        candidates = conn.execute(
            """
            SELECT en.id, en.country FROM entity_search s JOIN entities en ON en.rowid = s.rowid
            WHERE entity_search MATCH ? AND en.source_id = ? AND en.kind IN ('company','organization')
            ORDER BY bm25(entity_search) LIMIT 25
            """,
            (query, target),
        ).fetchall()
        scored = []
        for cand in candidates:
            raw, left_name, right_name = best_name_score(a_names, _names(conn, cand["id"]))
            score = adjusted_score(raw, ent["country"], cand["country"])
            if score >= threshold:
                scored.append((score, cand["id"], left_name, right_name, raw, cand["country"]))
        scored.sort(reverse=True)
        if not scored:
            continue
        with db.transaction(conn):
            for score, cid, left_name, right_name, raw, ccountry in scored[:max_per_entity]:
                features = {
                    "name_score": round(raw, 1), "adjusted_score": round(score, 1), "left_name": left_name,
                    "right_name": right_name, "country_left": ent["country"], "country_right": ccountry,
                }
                upsert_candidate(conn, ent["id"], cid, "name_fuzzy", round(score / 100, 3), features, "pending")
                count += 1
    return count


def screen(conn: sqlite3.Connection, *, source: str = "gleif", target: str = "opensanctions", fuzzy: bool = True,
           threshold: float = FUZZY_THRESHOLD) -> dict:
    stats: Counter = Counter()
    stats["identifier_matches"] = match_identifiers(conn)
    if fuzzy:
        stats["fuzzy_candidates"] = match_names(conn, source, target, threshold)
    stats["same_as_edges"] = conn.execute(
        "SELECT COUNT(*) FROM relationships WHERE predicate='same_as'"
    ).fetchone()[0]
    return dict(stats)


def review(conn: sqlite3.Connection, match_id: int, decision: str, note: str | None = None) -> dict:
    status = {"confirm": "confirmed", "reject": "rejected", "reset": "pending"}.get(decision)
    if status is None:
        raise ValueError("decision must be confirm, reject or reset")
    with db.transaction(conn):
        cur = conn.execute(
            "UPDATE match_candidates SET status=?, reviewer_note=?, reviewed_at=? WHERE id=?",
            (status, note, db.utcnow(), match_id),
        )
        if cur.rowcount == 0:
            raise KeyError(match_id)
        sync_same_as(conn, match_id)
    return dict(conn.execute("SELECT * FROM match_candidates WHERE id=?", (match_id,)).fetchone())
