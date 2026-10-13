"""Grounded AI explanations: deterministic retrieval -> compact evidence pack -> ONE request -> citation validation.

The model never sees the database, only up to MAX_EVIDENCE short excerpts labelled [E1]..[En]. Claims without a
valid citation are dropped and reported. If AI is unavailable, the deterministic explanation is returned on its own.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from .. import db
from ..connectors.opensanctions import OFFICIAL_PREFIXES
from ..graph.paths import edge_label, entity_summaries, find_paths
from .manager import INTERACTIVE, AIManager, AIUnavailable, get_manager

MAX_EVIDENCE = 24
EXCERPT_CHARS = 280
BASES = ("documented", "reported", "inferred")
CITE_RE = re.compile(r"\bE\d{1,3}\b")
CAVEAT = ("Connections, matches and shared attributes are leads for review, not evidence of wrongdoing. "
          "'same_as' links are algorithmic matches until an analyst confirms them.")

SYSTEM_PROMPT = """You are the analyst assistant of a financial-crime intelligence tool.
Rules:
1. Use ONLY the numbered evidence items ([E1], [E2], ...) and the deterministic facts in the prompt. No outside knowledge.
2. Every claim must list at least one evidence ID in "citations". Never cite an ID that is not listed.
3. Label each claim's basis: "documented" (official registry or list record), "reported" (secondary or aggregated
   source) or "inferred" (algorithmic match, or your own reasoning over the evidence). Hypotheses are "inferred".
4. A relationship, shared address, name match or sanctions proximity is NOT proof of wrongdoing. Never state or imply
   that a person or company committed a crime.
5. State uncertainties and contradictions explicitly, and suggest public records that would confirm or refute them.
Reply with ONE JSON object and nothing else:
{"summary": "...", "claims": [{"text": "...", "citations": ["E1"], "basis": "documented"}], "uncertainties": ["..."], "next_steps": ["..."]}"""


class EvidencePack:
    def __init__(self, limit: int = MAX_EVIDENCE):
        self.limit = limit
        self.items: list[dict] = []
        self._ids: dict[str, str] = {}

    def add(self, row: dict, about: str, basis: str) -> str | None:
        key = row["key"]
        if key in self._ids:
            return self._ids[key]
        if len(self.items) >= self.limit:
            return None
        eid = f"E{len(self.items) + 1}"
        self._ids[key] = eid
        self.items.append({
            "eid": eid, "evidence_id": row["id"], "key": key, "about": about, "basis": basis,
            "source_id": row["source_id"], "url": row.get("url"), "title": row.get("title"),
            "excerpt": (row.get("excerpt") or "")[:EXCERPT_CHARS], "entity_id": row.get("entity_id"),
            "relationship_id": row.get("relationship_id"), "raw_record_id": row.get("raw_record_id"),
            "retrieved_at": row.get("retrieved_at"),
        })
        return eid

    def by_id(self) -> dict[str, dict]:
        return {item["eid"]: item for item in self.items}

    def render(self) -> str:
        return "\n".join(
            f"[{i['eid']}] ({i['basis']}; source={i['source_id']}) {i['about']} :: {i['title'] or ''} :: {i['excerpt']}"
            for i in self.items
        )

    def fingerprint(self) -> str:
        return hashlib.sha256(self.render().encode("utf-8")).hexdigest()[:16]


def cite(refs) -> str:
    return "".join(f"[{r}]" for r in refs if r)


def entity_basis(source_id: str, attrs: dict) -> str:
    if source_id == "resolver":
        return "inferred"
    if source_id == "opensanctions":
        datasets = attrs.get("datasets") or []
        return "documented" if any(str(d).startswith(OFFICIAL_PREFIXES) for d in datasets) else "reported"
    return "documented"


def _entity_basis(conn: sqlite3.Connection, entity_id: str) -> str:
    row = conn.execute("SELECT source_id, attrs FROM entities WHERE id=?", (entity_id,)).fetchone()
    return entity_basis(row["source_id"], db.loads(row["attrs"], {})) if row else "reported"


def evidence_for_relationship(conn: sqlite3.Connection, rel_id: int, limit: int = 2) -> list[dict]:
    return db.rows(conn.execute("SELECT * FROM evidence WHERE relationship_id=? ORDER BY id LIMIT ?", (rel_id, limit)))


def evidence_for_entity(conn: sqlite3.Connection, entity_id: str, limit: int = 3) -> list[dict]:
    return db.rows(conn.execute(
        "SELECT * FROM evidence WHERE entity_id=? AND relationship_id IS NULL ORDER BY id LIMIT ?", (entity_id, limit)
    ))


def describe_path(path: dict, index: int) -> str:
    names = [n.get("name", n["id"]) for n in path["nodes"]]
    parts = [names[0]]
    for step, name in zip(path["edges"], names[1:]):
        parts.append(f"-[{step['label']} | {step['basis']}, {step['source_id']}]{cite(step.get('evidence_refs', []))}-> {name}")
    text = f"Path {index} ({path['hops']} hop(s), cost {path['cost']}): " + " ".join(parts)
    if path.get("via_hubs"):
        hubs = {n["id"]: n.get("name", n["id"]) for n in path["nodes"]}
        text += f". Passes through highly connected node(s): {', '.join(hubs.get(h, h) for h in path['via_hubs'])}"
    return text


def _describe_entity(e: dict) -> str:
    topics = ", ".join(e.get("topics") or []) or "none"
    return f"{e['name']} ({e['id']}; {e.get('kind')}; country {e.get('country') or 'n/a'}; topics {topics})"


# --------------------------------------------------------------------------- answer validation

def parse_answer(text: str) -> dict | None:
    cleaned = (text or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.S)
    if fence:
        cleaned = fence.group(1)
    candidates = [cleaned]
    if "{" in cleaned and "}" in cleaned:
        candidates.append(cleaned[cleaned.find("{"): cleaned.rfind("}") + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _citations(values, text: str) -> list[str]:
    found: list[str] = []
    listed = values if isinstance(values, list) else []
    for value in [*listed, *CITE_RE.findall(text or "")]:
        token = str(value).strip().strip("[]").upper()
        if token and token not in found:
            found.append(token)
    return found


def _text_list(value, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v)[:300] for v in value if str(v).strip()][:limit]


def validate_answer(text: str, pack: EvidencePack) -> dict:
    known = pack.by_id()
    data = parse_answer(text)
    fmt = "json"
    if data is None:
        fmt = "text"
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()]
        data = {"summary": None, "claims": [{"text": s, "citations": [], "basis": "inferred"} for s in sentences]}
    claims, rejected, unknown_all, downgraded = [], [], [], 0
    for raw in data.get("claims") or []:
        if not isinstance(raw, dict):
            continue
        claim_text = str(raw.get("text") or "").strip()
        cites = _citations(raw.get("citations"), claim_text)
        unknown = [c for c in cites if c not in known]
        valid = [c for c in cites if c in known]
        unknown_all += [c for c in unknown if c not in unknown_all]
        if not claim_text or not valid or unknown:
            reason = f"cites unknown evidence {', '.join(unknown)}" if unknown else "no citation to retrieved evidence"
            rejected.append({"text": claim_text[:400], "citations": cites, "reason": reason})
            continue
        basis = str(raw.get("basis") or "inferred").lower()
        basis = basis if basis in BASES else "inferred"
        support = {known[c]["basis"] for c in valid}
        strongest = next(b for b in BASES if b in support or b == "inferred")
        if BASES.index(basis) < BASES.index(strongest):
            basis = strongest
            downgraded += 1
        claims.append({"text": claim_text[:600], "citations": valid, "basis": basis})
    summary = str(data.get("summary") or "").strip() or None
    summary_unknown = [c for c in _citations([], summary or "") if c not in known]
    if summary_unknown:
        unknown_all += [c for c in summary_unknown if c not in unknown_all]
        summary = None
    total = len(claims) + len(rejected)
    return {
        "summary": summary[:1200] if summary else None,
        "claims": claims,
        "rejected_claims": rejected,
        "uncertainties": _text_list(data.get("uncertainties")),
        "next_steps": _text_list(data.get("next_steps")),
        "citation_check": {
            "format": fmt, "total_claims": total, "accepted": len(claims), "rejected": len(rejected),
            "unknown_citations": unknown_all, "downgraded": downgraded, "summary_dropped": bool(summary_unknown),
            "citation_precision": round(len(claims) / total, 3) if total else None,
        },
        "disclaimer": "Model-written text limited to the cited evidence. Verify against the linked sources; "
                      "this is not a finding of wrongdoing.",
    }


def run_ai(manager: AIManager | None, task: str, prompt: str, pack: EvidencePack, priority: int = INTERACTIVE) -> dict:
    manager = manager or get_manager()
    try:
        res = manager.complete(task, SYSTEM_PROMPT, prompt, fingerprint=pack.fingerprint(), priority=priority)
    except AIUnavailable as exc:
        manager.bump("fallbacks")
        return {"ai": None, "ai_status": {"used": False, "reason": exc.reason, "message": str(exc),
                                          "retry_after": exc.retry_after}}
    return {
        "ai": validate_answer(res.text, pack),
        "ai_status": {"used": True, "cached": res.cached, "deduplicated": res.deduplicated, "model": res.model,
                      "attempts": res.attempts, "usage": res.usage},
    }


# --------------------------------------------------------------------------- tasks

def explain_path(conn: sqlite3.Connection, a: str, b: str, *, k: int = 3, max_depth: int = 6,
                 question: str | None = None, use_ai: bool = True, manager: AIManager | None = None,
                 priority: int = INTERACTIVE) -> dict:
    ends = entity_summaries(conn, [a, b])
    for entity_id in (a, b):
        if entity_id not in ends:
            raise KeyError(entity_id)
    paths = find_paths(conn, a, b, k=k, max_depth=max_depth)
    pack = EvidencePack()
    for path in paths:
        names = {n["id"]: n.get("name", n["id"]) for n in path["nodes"]}
        for step in path["edges"]:
            about = f"{names.get(step['from'])} {step['label']} {names.get(step['to'])}"
            refs = [pack.add(row, about, step["basis"]) for row in evidence_for_relationship(conn, step["id"])]
            step["evidence_refs"] = [r for r in refs if r]
        for node in path["nodes"]:
            if node.get("sanctioned") or node["id"] in (a, b):
                basis = _entity_basis(conn, node["id"])
                refs = [pack.add(row, f"record for {node.get('name')}", basis)
                        for row in evidence_for_entity(conn, node["id"], 2)]
                node["evidence_refs"] = [r for r in refs if r]
    lines = [describe_path(p, i + 1) for i, p in enumerate(paths)]
    name_a, name_b = ends[a]["name"], ends[b]["name"]
    summary = (f"{len(paths)} evidence-backed path(s) connect {name_a} and {name_b} within {max_depth} hops."
               if paths else f"No path connects {name_a} and {name_b} within {max_depth} hops in the loaded data.")
    result = {
        "task": "explain_path", "a": ends[a], "b": ends[b], "paths": paths,
        "deterministic": {"summary": summary, "lines": lines, "caveat": CAVEAT},
        "evidence": pack.items, "ai": None,
    }
    if not paths:
        result["ai_status"] = {"used": False, "reason": "no_path", "message": "No path found, so no AI request was made."}
        return result
    if not use_ai:
        result["ai_status"] = {"used": False, "reason": "not_requested"}
        return result
    prompt = "\n".join(filter(None, [
        "TASK: Explain how entity A and entity B are connected, using only the evidence below.",
        f"ENTITY A: {_describe_entity(ends[a])}",
        f"ENTITY B: {_describe_entity(ends[b])}",
        "DETERMINISTIC PATHS (graph engine; cheapest first; highly connected hubs penalised):",
        *lines,
        "EVIDENCE:",
        pack.render(),
        f"ANALYST QUESTION: {question.strip()[:500]}" if question and question.strip() else None,
    ]))
    result.update(run_ai(manager, "explain_path", prompt, pack, priority))
    return result


def entity_brief(conn: sqlite3.Connection, entity_id: str, *, question: str | None = None, use_ai: bool = True,
                 manager: AIManager | None = None, priority: int = INTERACTIVE, max_relationships: int = 15) -> dict:
    row = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
    if row is None:
        raise KeyError(entity_id)
    e = dict(row)
    attrs = db.loads(e["attrs"], {})
    topics = [t for t in (e["topics"] or "").split(",") if t]
    pack = EvidencePack()
    own_refs = [r for r in (pack.add(ev, f"record for {e['name']}", entity_basis(e["source_id"], attrs))
                            for ev in evidence_for_entity(conn, entity_id, 4)) if r]
    facts = [f"{e['name']} ({e['kind']}; source {e['source_id']}; country {e['country'] or 'n/a'}; "
             f"status {e['status'] or 'n/a'}){cite(own_refs[:1])}"]
    if topics:
        facts.append(f"Topics recorded by the source: {', '.join(topics)}{cite(own_refs)}")
    idents = conn.execute(
        "SELECT scheme, value FROM identifiers WHERE entity_id=? ORDER BY scheme LIMIT 12", (entity_id,)
    ).fetchall()
    if idents:
        facts.append("Identifiers: " + ", ".join(f"{i['scheme']}={i['value']}" for i in idents) + cite(own_refs[:1]))
    rels = conn.execute(
        "SELECT * FROM relationships WHERE subject_id=? OR object_id=? ORDER BY CASE basis WHEN 'documented' THEN 0 "
        "WHEN 'reported' THEN 1 ELSE 2 END, confidence DESC, id LIMIT ?",
        (entity_id, entity_id, max_relationships),
    ).fetchall()
    others = entity_summaries(conn, {r["object_id"] if r["subject_id"] == entity_id else r["subject_id"] for r in rels})
    for r in rels:
        forward = r["subject_id"] == entity_id
        other = others.get(r["object_id"] if forward else r["subject_id"], {})
        about = f"{e['name']} {edge_label(r['predicate'], forward)} {other.get('name', '?')}"
        refs = [x for x in (pack.add(ev, about, r["basis"]) for ev in evidence_for_relationship(conn, r["id"], 1)) if x]
        flag = " (counterpart has a sanction topic)" if other.get("sanctioned") else ""
        dates = f", from {r['start_date']}" if r["start_date"] else ""
        facts.append(f"{about} [{r['basis']}, confidence {r['confidence']:.2f}{dates}]{flag}{cite(refs)}")
    matches = conn.execute(
        "SELECT * FROM match_candidates WHERE (left_id=? OR right_id=?) AND status != 'rejected' "
        "ORDER BY score DESC LIMIT 5",
        (entity_id, entity_id),
    ).fetchall()
    counterpart = entity_summaries(conn, {m["right_id"] if m["left_id"] == entity_id else m["left_id"] for m in matches})
    for m in matches:
        other = counterpart.get(m["right_id"] if m["left_id"] == entity_id else m["left_id"], {})
        facts.append(f"Possible same entity: {other.get('name', '?')} (match #{m['id']}, {m['method']}, "
                     f"score {m['score']:.2f}, status {m['status']}) [inferred]")
    summary = (f"{e['name']}: {len(rels)} relationship(s) listed, {len(topics)} topic flag(s), "
               f"{len(matches)} open or accepted match candidate(s).")
    result = {
        "task": "entity_brief", "entity": entity_summaries(conn, [entity_id])[entity_id],
        "deterministic": {"summary": summary, "lines": facts, "caveat": CAVEAT},
        "evidence": pack.items, "ai": None,
    }
    if not use_ai:
        result["ai_status"] = {"used": False, "reason": "not_requested"}
        return result
    prompt = "\n".join(filter(None, [
        "TASK: Write a short investigative profile of this entity using only the evidence below. Separate documented "
        "facts from reported or inferred points, and list what should be checked next.",
        "FACTS (deterministic):",
        *facts,
        "EVIDENCE:",
        pack.render(),
        f"ANALYST QUESTION: {question.strip()[:500]}" if question and question.strip() else None,
    ]))
    result.update(run_ai(manager, "entity_brief", prompt, pack, priority))
    return result
