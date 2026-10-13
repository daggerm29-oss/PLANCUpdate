"""FastAPI service. Run: `uvicorn app.api:app --port 8000` from backend/.
Only /assistant/*, /paths?ai=1 and /reports?ai=1 can spend a model request; everything else is pure SQL."""
from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import db
from .ai.assistant import entity_brief, explain_path
from .ai.manager import get_manager
from .connectors import mca_upload
from .connectors.crawler import get_client
from .graph.analytics import all_leads, conflicts, timeline
from .graph.paths import edge_label, entity_summaries, ownership_chain
from .intel.claims import claims_for
from .intel.service import build_dossier, investigate, search_entities
from .reports import build_report
from .resolution import matcher
from .settings import settings

app = FastAPI(title="OSINT Financial Intelligence API", version="0.3.0")

# Allow Vercel frontend, localhost, and custom domains from CORS_ORIGINS
_cors_list = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_list if "*" not in _cors_list else ["*"],
    allow_origin_regex=r"https://.*\.vercel\.app" if "*" not in _cors_list else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


Conn = Depends(get_conn)


def _entity_or_404(conn: sqlite3.Connection, entity_id: str) -> dict:
    row = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"entity not found: {entity_id}")
    e = dict(row)
    e["attrs"] = db.loads(e["attrs"], {})
    e["topics"] = [t for t in e["topics"].split(",") if t]
    return e


# --------------------------------------------------------------------------- system

@app.get("/api/health")
def health(db_check: bool = Query(False, alias="db")):
    """Health check endpoint.

    Lightweight by default to serve 5-minute UptimeRobot pings without consuming
    MotherDuck cloud compute credits. Pass ?db=1 for deep verification.
    """
    res = {
        "ok": True,
        "status": "healthy",
        "service": "PLANC Financial Intelligence API",
        "backend": settings.db_backend,
        "uptime_ping_safe": True,
    }
    if db_check:
        if settings.db_backend in ("motherduck", "duckdb"):
            try:
                from . import duckdb_db
                con = duckdb_db.connect()
                res["counts"] = duckdb_db.counts(con)
                res["database"] = "MotherDuck (hosted)" if duckdb_db.is_motherduck() else "DuckDB (local)"
            except Exception as e:
                res["db_error"] = str(e)
        else:
            conn = db.connect()
            db.init_db(conn)
            res["counts"] = db.counts(conn)
            res["database"] = "SQLite"
            conn.close()
    return res


@app.get("/api/stats")
def stats(conn: sqlite3.Connection = Conn):
    if settings.db_backend in ("motherduck", "duckdb"):
        try:
            from . import duckdb_db
            con = duckdb_db.connect()
            duckdb_db.init_db(con)
            return {
                "backend": "MotherDuck (hosted)" if duckdb_db.is_motherduck() else "DuckDB (local)",
                "counts": duckdb_db.counts(con),
                "sources": duckdb_db.get_sources_summary(con),
                "runs": [],
                "pending_reviews": 0,
            }
        except Exception:
            pass  # fallback to SQLite

    sources = db.rows(conn.execute(
        "SELECT s.id, s.name, s.homepage, s.licence, (SELECT COUNT(*) FROM entities e WHERE e.source_id=s.id) AS entities "
        "FROM sources s ORDER BY s.id"))
    runs = db.rows(conn.execute(
        "SELECT id, source_id, started_at, finished_at, status, stats, error FROM ingest_runs ORDER BY id DESC LIMIT 20"))
    for r in runs:
        r["stats"] = db.loads(r["stats"], {})
    pending = conn.execute("SELECT COUNT(*) FROM match_candidates WHERE status='pending'").fetchone()[0]
    return {"backend": "SQLite", "counts": db.counts(conn), "sources": sources, "runs": runs, "pending_reviews": pending}


@app.get("/api/ai/status")
def ai_status():
    return get_manager().status()



@app.get("/api/ai/auth/status")
def ai_auth_status():
    mgr = get_manager()
    prov = mgr.provider
    if hasattr(prov, "load_tokens"):
        tokens = prov.load_tokens()
        if not tokens or not tokens.get("access_token"):
            return {"status": "not_connected", "account": None, "selected_model": None, "available_models": []}
        exp = prov._expires_at(tokens)
        now = time.time() if "time" in globals() else 0
        import time as _t
        if exp and exp < _t.time():
            if tokens.get("refresh_token"):
                return {"status": "expired", "account": {"email": tokens.get("email"), "account_id": prov.account_id(tokens)},
                        "selected_model": tokens.get("selected_model", prov.model), "available_models": tokens.get("available_models", []),
                        "expires_at": exp}
            return {"status": "expired", "account": None, "selected_model": None, "available_models": []}
        return {
            "status": "connected",
            "account": {
                "email": tokens.get("email"),
                "account_id": prov.account_id(tokens),
            },
            "selected_model": tokens.get("selected_model") or prov.model,
            "available_models": tokens.get("available_models") or [prov.model],
            "expires_at": exp,
        }
    return {"status": "not_connected", "account": None, "selected_model": None, "available_models": []}


class SelectModelReq(BaseModel):
    model: str


@app.post("/api/ai/auth/select-model")
def ai_select_model(req: SelectModelReq):
    prov = get_manager().provider
    if hasattr(prov, "set_model"):
        ok = prov.set_model(req.model)
        if ok:
            return {"ok": True, "selected_model": req.model}
    raise HTTPException(400, "Unable to select model (not logged in or provider unsupported)")


@app.post("/api/ai/auth/refresh-models")
def ai_refresh_models():
    prov = get_manager().provider
    if hasattr(prov, "discover_models"):
        try:
            models = prov.discover_models()
            return {"ok": True, "models": models, "selected_model": prov.model}
        except Exception as exc:
            raise HTTPException(400, f"Failed to refresh models: {exc}")
    raise HTTPException(400, "Provider does not support model discovery")


@app.post("/api/ai/auth/disconnect")
def ai_disconnect():
    prov = get_manager().provider
    if hasattr(prov, "disconnect"):
        prov.disconnect()
        return {"ok": True}
    raise HTTPException(400, "Provider does not support disconnect")


# --------------------------------------------------------------------------- search & entities

@app.get("/api/search")
def search(q: str = Query(..., min_length=1, max_length=200), limit: int = Query(25, ge=1, le=100),
           conn: sqlite3.Connection = Conn):
    if settings.db_backend in ("motherduck", "duckdb"):
        try:
            from . import duckdb_db
            con = duckdb_db.connect()
            duck_res = duckdb_db.search(con, q, limit=limit)
            if duck_res:
                return {
                    "query": q,
                    "backend": "MotherDuck (hosted)" if duckdb_db.is_motherduck() else "DuckDB (local)",
                    "detected_identifiers": [],
                    "results": duck_res,
                }
        except Exception:
            pass
    ids, detected = search_entities(conn, q, limit)
    found = entity_summaries(conn, ids)
    return {"query": q, "detected_identifiers": [{"scheme": s, "value": v} for s, v in detected],
            "results": [found[i] for i in ids if i in found]}


@app.get("/api/entities/{entity_id:path}/graph")
def entity_graph(entity_id: str, limit: int = Query(60, ge=1, le=300), conn: sqlite3.Connection = Conn):
    if settings.db_backend in ("motherduck", "duckdb"):
        try:
            from . import duckdb_db
            con = duckdb_db.connect()
            if duckdb_db.get_entity(con, entity_id):
                g = duckdb_db.get_graph(con, entity_id)
                for r in g["edges"]:
                    r["label"] = edge_label(r["predicate"])
                return {"root": entity_id, "nodes": g["nodes"], "edges": g["edges"]}
        except Exception:
            pass
    _entity_or_404(conn, entity_id)
    rels = db.rows(conn.execute(
        "SELECT id, source_id, subject_id, predicate, object_id, basis, confidence, start_date, end_date "
        "FROM relationships WHERE subject_id=? OR object_id=? LIMIT ?", (entity_id, entity_id, limit)))
    nodes = entity_summaries(conn, {entity_id} | {r["subject_id"] for r in rels} | {r["object_id"] for r in rels})
    for r in rels:
        r["label"] = edge_label(r["predicate"])
    return {"root": entity_id, "nodes": list(nodes.values()), "edges": rels}


@app.get("/api/entities/{entity_id:path}/ownership")
def entity_ownership(entity_id: str, direction: str = Query("up", pattern="^(up|down)$"),
                     conn: sqlite3.Connection = Conn):
    if settings.db_backend in ("motherduck", "duckdb"):
        try:
            from . import duckdb_db
            con = duckdb_db.connect()
            if duckdb_db.get_entity(con, entity_id):
                return duckdb_db.get_ownership(con, entity_id)
        except Exception:
            pass
    _entity_or_404(conn, entity_id)
    return ownership_chain(conn, entity_id, direction)


@app.get("/api/entities/{entity_id:path}/timeline")
def entity_timeline(entity_id: str, conn: sqlite3.Connection = Conn):
    if settings.db_backend in ("motherduck", "duckdb"):
        try:
            from . import duckdb_db
            con = duckdb_db.connect()
            if duckdb_db.get_entity(con, entity_id):
                return {"events": duckdb_db.get_timeline(con, entity_id), "conflicts": []}
        except Exception:
            pass
    _entity_or_404(conn, entity_id)
    return {"events": timeline(conn, entity_id), "conflicts": conflicts(conn, entity_id)}


@app.get("/api/entities/{entity_id:path}/report")
def entity_report(entity_id: str, ai: bool = False, conn: sqlite3.Connection = Conn):
    _entity_or_404(conn, entity_id)
    return build_report(conn, entity_id, use_ai=ai)


@app.get("/api/entities/{entity_id:path}")
def entity_detail(entity_id: str, conn: sqlite3.Connection = Conn):
    if settings.db_backend in ("motherduck", "duckdb"):
        try:
            from . import duckdb_db
            con = duckdb_db.connect()
            ent = duckdb_db.get_entity(con, entity_id)
            if ent:
                g = duckdb_db.get_graph(con, entity_id)
                ent["relationships"] = g["edges"]
                ent["evidence"] = []
                ent["matches"] = []
                ent["claims"] = []
                return ent
        except Exception:
            pass
    e = _entity_or_404(conn, entity_id)
    names = db.rows(conn.execute("SELECT name, name_type FROM entity_names WHERE entity_id=?", (entity_id,)))
    idents = db.rows(conn.execute("SELECT scheme, value FROM identifiers WHERE entity_id=?", (entity_id,)))

    rels = db.rows(conn.execute(
        "SELECT id, source_id, subject_id, predicate, object_id, basis, confidence, start_date, end_date, status "
        "FROM relationships WHERE subject_id=? OR object_id=? ORDER BY basis, confidence DESC LIMIT 200",
        (entity_id, entity_id)))
    others = entity_summaries(conn, {r["object_id"] if r["subject_id"] == entity_id else r["subject_id"] for r in rels})
    for r in rels:
        forward = r["subject_id"] == entity_id
        r["forward"] = forward
        r["label"] = edge_label(r["predicate"], forward)
        r["other"] = others.get(r["object_id"] if forward else r["subject_id"])
    evidence = db.rows(conn.execute("SELECT * FROM evidence WHERE entity_id=? LIMIT 50", (entity_id,)))
    matches = db.rows(conn.execute(
        "SELECT id, left_id, right_id, method, score, status FROM match_candidates WHERE left_id=? OR right_id=?",
        (entity_id, entity_id)))
    return {"entity": e, "names": names, "identifiers": idents, "relationships": rels,
            "evidence": evidence, "matches": matches}


@app.get("/api/relationships/{rel_id}")
def relationship_detail(rel_id: int, conn: sqlite3.Connection = Conn):
    row = conn.execute("SELECT * FROM relationships WHERE id=?", (rel_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "relationship not found")
    rel = dict(row)
    rel["attrs"] = db.loads(rel["attrs"], {})
    rel["label"] = edge_label(rel["predicate"])
    ends = entity_summaries(conn, [rel["subject_id"], rel["object_id"]])
    evidence = db.rows(conn.execute("SELECT * FROM evidence WHERE relationship_id=?", (rel_id,)))
    raw = None
    if rel.get("raw_record_id"):
        r = conn.execute("SELECT id, source_id, record_key, url, last_fetched_at, last_changed_at, payload "
                         "FROM raw_records WHERE id=?",
                         (rel["raw_record_id"],)).fetchone()
        raw = dict(r) if r else None
    return {"relationship": rel, "subject": ends.get(rel["subject_id"]), "object": ends.get(rel["object_id"]),
            "evidence": evidence, "raw_record": raw}


# --------------------------------------------------------------------------- analysis

@app.get("/api/paths")
def paths(a: str, b: str, k: int = Query(3, ge=1, le=5), max_depth: int = Query(6, ge=1, le=8), ai: bool = False,
          question: str | None = None, conn: sqlite3.Connection = Conn):
    try:
        return explain_path(conn, a, b, k=k, max_depth=max_depth, question=question, use_ai=ai)
    except KeyError as exc:
        raise HTTPException(404, f"entity not found: {exc.args[0]}") from exc


@app.get("/api/leads")
def leads(type: str | None = None, limit: int = Query(50, ge=1, le=200), conn: sqlite3.Connection = Conn):
    return {"leads": all_leads(conn, type, limit)}


class BriefRequest(BaseModel):
    entity_id: str
    question: str | None = Field(None, max_length=500)
    use_ai: bool = True


@app.post("/api/assistant/brief")
def assistant_brief(req: BriefRequest, conn: sqlite3.Connection = Conn):
    _entity_or_404(conn, req.entity_id)
    return entity_brief(conn, req.entity_id, question=req.question, use_ai=req.use_ai)


# --------------------------------------------------------------------------- intelligence + crawler

class InvestigateRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    entity_id: str | None = Field(None, max_length=200)
    crawl: bool = False
    force_refresh: bool = False


@app.post("/api/intel/investigate")
def intel_investigate(req: InvestigateRequest, conn: sqlite3.Connection = Conn):
    """Only this endpoint (with crawl=true) can spend crawler / Serper credits."""
    return investigate(conn, req.query, entity_id=req.entity_id, crawl=req.crawl, force_refresh=req.force_refresh)


@app.get("/api/intel/dossier/{entity_id:path}")
def intel_dossier(entity_id: str, conn: sqlite3.Connection = Conn):
    _entity_or_404(conn, entity_id)
    return build_dossier(conn, entity_id)


@app.get("/api/intel/claims")
def intel_claims(entity_id: str | None = None, query: str | None = None, conn: sqlite3.Connection = Conn):
    if not entity_id and not query:
        raise HTTPException(400, "entity_id or query is required")
    return {"claims": claims_for(conn, entity_id=entity_id, query=query)}


@app.get("/api/crawler/health")
def crawler_health():
    return get_client().health()


@app.get("/api/crawler/usage")
def crawler_usage():
    return get_client().usage()


@app.post("/api/sources/mca/upload", status_code=202)
async def mca_upload_start(request: Request, filename: str = Query(..., min_length=5, max_length=200)):
    """Raw request body = the file (Content-Type application/octet-stream). Streamed to disk, then loaded in the
    background; poll /api/sources/mca/jobs/{id}. Spends no crawler credits or AI requests."""
    try:
        path = await mca_upload.save_stream(filename, request.stream())
        files = await run_in_threadpool(mca_upload.prepare, path)
        return mca_upload.start(filename, files)
    except mca_upload.UploadError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/sources/mca/jobs")
def mca_upload_jobs():
    return {"jobs": mca_upload.recent()}


@app.get("/api/sources/mca/jobs/{job_id}")
def mca_upload_job(job_id: str):
    j = mca_upload.job(job_id)
    if j is None:
        raise HTTPException(404, "unknown upload job (jobs are kept in memory until the API restarts)")
    return j


# --------------------------------------------------------------------------- review queue

@app.get("/api/review")
def review_queue(status: str = Query("pending", pattern="^(pending|auto|confirmed|rejected)$"),
                 limit: int = Query(50, ge=1, le=200), conn: sqlite3.Connection = Conn):
    items = db.rows(conn.execute(
        "SELECT * FROM match_candidates WHERE status=? ORDER BY score DESC LIMIT ?", (status, limit)))
    ents = entity_summaries(conn, {i["left_id"] for i in items} | {i["right_id"] for i in items})
    for i in items:
        i["features"] = db.loads(i["features"], {})
        i["left"], i["right"] = ents.get(i["left_id"]), ents.get(i["right_id"])
    return {"items": items}


class ReviewDecision(BaseModel):
    decision: str = Field(..., pattern="^(confirm|reject|reset)$")
    note: str | None = Field(None, max_length=500)


@app.post("/api/review/{match_id}")
def review_decide(match_id: int, body: ReviewDecision, conn: sqlite3.Connection = Conn):
    try:
        return matcher.review(conn, match_id, body.decision, body.note)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


# --------------------------------------------------------------------------- investigations

class InvestigationIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    notes: str | None = Field(None, max_length=5000)


class ItemIn(BaseModel):
    item_type: str = Field(..., pattern="^(entity|relationship|path|note)$")
    ref: str = Field(..., min_length=1, max_length=500)
    note: str | None = Field(None, max_length=2000)


@app.get("/api/investigations")
def list_investigations(conn: sqlite3.Connection = Conn):
    return {"items": db.rows(conn.execute(
        "SELECT i.*, (SELECT COUNT(*) FROM investigation_items x WHERE x.investigation_id=i.id) AS item_count "
        "FROM investigations i ORDER BY updated_at DESC"))}


@app.post("/api/investigations")
def create_investigation(body: InvestigationIn, conn: sqlite3.Connection = Conn):
    now = db.utcnow()
    cur = conn.execute("INSERT INTO investigations(title, notes, created_at, updated_at) VALUES (?,?,?,?)",
                       (body.title.strip(), body.notes, now, now))
    return get_investigation(int(cur.lastrowid), conn)


@app.get("/api/investigations/{inv_id}")
def get_investigation(inv_id: int, conn: sqlite3.Connection = Conn):
    row = conn.execute("SELECT * FROM investigations WHERE id=?", (inv_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "investigation not found")
    items = db.rows(conn.execute(
        "SELECT * FROM investigation_items WHERE investigation_id=? ORDER BY id", (inv_id,)))
    ents = entity_summaries(conn, [i["ref"] for i in items if i["item_type"] == "entity"])
    for i in items:
        i["entity"] = ents.get(i["ref"]) if i["item_type"] == "entity" else None
    return {**dict(row), "items": items}


@app.post("/api/investigations/{inv_id}/items")
def add_investigation_item(inv_id: int, body: ItemIn, conn: sqlite3.Connection = Conn):
    if conn.execute("SELECT 1 FROM investigations WHERE id=?", (inv_id,)).fetchone() is None:
        raise HTTPException(404, "investigation not found")
    if body.item_type == "entity":
        _entity_or_404(conn, body.ref)
    now = db.utcnow()
    conn.execute("INSERT INTO investigation_items(investigation_id, item_type, ref, note, created_at) VALUES (?,?,?,?,?)",
                 (inv_id, body.item_type, body.ref, body.note, now))
    conn.execute("UPDATE investigations SET updated_at=? WHERE id=?", (now, inv_id))
    return get_investigation(inv_id, conn)


@app.delete("/api/investigations/{inv_id}/items/{item_id}")
def delete_investigation_item(inv_id: int, item_id: int, conn: sqlite3.Connection = Conn):
    conn.execute("DELETE FROM investigation_items WHERE id=? AND investigation_id=?", (item_id, inv_id))
    return get_investigation(inv_id, conn)
