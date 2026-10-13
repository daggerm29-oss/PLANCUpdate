"""Evidence-backed Markdown report for one entity. Deterministic sections always render; the AI section is
optional, costs at most one model request (cached), and is dropped silently if AI is unavailable."""
from __future__ import annotations

import sqlite3

from .ai.assistant import CAVEAT, entity_brief
from .ai.manager import AIManager
from .graph.analytics import conflicts, timeline
from .graph.paths import ownership_chain


def _evidence_line(ev: dict) -> str:
    link = f" <{ev['url']}>" if ev.get("url") else ""
    return (f"- **[{ev['eid']}]** ({ev['source_id']}, {ev['basis']}) {ev.get('title') or ''}: "
            f"{ev.get('excerpt') or ''}{link}")


def build_report(conn: sqlite3.Connection, entity_id: str, *, use_ai: bool = False,
                 manager: AIManager | None = None, question: str | None = None) -> dict:
    brief = entity_brief(conn, entity_id, use_ai=use_ai, manager=manager, question=question)
    e = brief["entity"]
    det = brief["deterministic"]
    up = ownership_chain(conn, entity_id, "up")
    events = timeline(conn, entity_id)
    issues = conflicts(conn, entity_id)

    md = [f"# Investigation report: {e['name']}", "", f"> {CAVEAT}", "", "## Summary", det["summary"], "",
          "## Documented facts and leads"]
    md += [f"- {line}" for line in det["lines"]]
    md += ["", "## Ownership (upward)"]
    md += [f"- {'  ' * max(0, n['depth'] - 1)}{(n.get('edge') or {}).get('label', 'linked to')} "
           f"{n['entity']['name']} [{(n.get('edge') or {}).get('basis', 'n/a')}]"
           for n in up["nodes"]] or ["- None recorded"]
    md += ["", "## Timeline"]
    md += [f"- {ev['date']}: {ev['label']} ({ev['source_id']})" for ev in events] or ["- No dated events"]
    md += ["", "## Conflicts between linked records"]
    md += [f"- {c['field']}: " + "; ".join(f"{v['entity_id']}={v['value']}" for v in c["values"])
           for c in issues] or ["- None detected"]

    ai = brief.get("ai")
    if ai:
        md += ["", "## AI analysis (model-generated; citations validated)", ai.get("summary") or ""]
        md += [f"- [{c['basis']}] {c['text']} ({', '.join(c['citations'])})" for c in ai["claims"]]
        if ai["uncertainties"]:
            md += ["### Uncertainties"] + [f"- {x}" for x in ai["uncertainties"]]
        if ai["next_steps"]:
            md += ["### Suggested next checks"] + [f"- {x}" for x in ai["next_steps"]]
    md += ["", "## Evidence"] + [_evidence_line(ev) for ev in brief["evidence"]]
    return {"entity": e, "markdown": "\n".join(md), "ai_status": brief.get("ai_status"),
            "evidence": brief["evidence"]}
