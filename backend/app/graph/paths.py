"""Evidence-backed path discovery and ownership-chain reconstruction over SQL adjacency."""
from __future__ import annotations

import heapq
import itertools
import math
import sqlite3
from collections import defaultdict

LABELS = {
    "direct_parent": ("is directly consolidated by", "directly consolidates"),
    "ultimate_parent": ("is ultimately consolidated by", "ultimately consolidates"),
    "owns": ("owns", "is owned by"),
    "director_of": ("is director of", "has director"),
    "employed_by": ("is employed by", "employs"),
    "member_of": ("is member of", "has member"),
    "family_of": ("is family of", "is family of"),
    "associate_of": ("is associate of", "is associate of"),
    "represents": ("represents", "is represented by"),
    "linked_to": ("is linked to", "is linked to"),
    "succeeded_by": ("is succeeded by", "succeeds"),
    "same_as": ("may be the same entity as", "may be the same entity as"),
    "officer_of": ("is an officer of", "has officer"),
    "intermediary_of": ("is an intermediary for", "used intermediary"),
    "registered_address": ("is registered at", "is the registered address of"),
    "similar_to": ("may be the same as (per ICIJ)", "may be the same as (per ICIJ)"),
}
BASIS_COST = {"documented": 1.0, "reported": 1.6, "inferred": 0.4}
HUB_ALPHA = 0.5
HUB_DEGREE = 50
EDGE_COLUMNS = "id, source_id, subject_id, predicate, object_id, basis, confidence, start_date, end_date, status"


def edge_label(predicate: str, forward: bool = True) -> str:
    fwd, rev = LABELS.get(predicate, (predicate.replace("_", " "), f"{predicate.replace('_', ' ')} (reverse)"))
    return fwd if forward else rev


def edge_cost(edge: dict) -> float:
    return BASIS_COST.get(edge["basis"], 1.5) / max(0.05, float(edge["confidence"] or 0.05))


def entity_summaries(conn: sqlite3.Connection, ids) -> dict[str, dict]:
    ids = list(ids)
    out: dict[str, dict] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"SELECT id, name, kind, country, source_id, status, topics FROM entities WHERE id IN ({marks})", chunk
        ):
            topics = [t for t in (r["topics"] or "").split(",") if t]
            out[r["id"]] = {
                "id": r["id"], "name": r["name"], "kind": r["kind"], "country": r["country"],
                "source_id": r["source_id"], "status": r["status"], "topics": topics,
                "sanctioned": "sanction" in topics,
            }
    return out


class GraphView:
    """Lazy adjacency over `relationships` with per-query caches."""

    def __init__(self, conn: sqlite3.Connection, exclude_predicates=(), neighbor_limit: int = 1000):
        self.conn = conn
        self.exclude = set(exclude_predicates)
        self.neighbor_limit = neighbor_limit
        self._neighbors: dict[str, list[dict]] = {}
        self._degree: dict[str, int] = {}

    def neighbors(self, node: str) -> list[dict]:
        if node not in self._neighbors:
            rows = self.conn.execute(
                f"SELECT {EDGE_COLUMNS} FROM relationships WHERE subject_id=? "
                f"UNION ALL SELECT {EDGE_COLUMNS} FROM relationships WHERE object_id=? LIMIT ?",
                (node, node, self.neighbor_limit),
            ).fetchall()
            self._neighbors[node] = [dict(r) for r in rows if r["predicate"] not in self.exclude]
        return self._neighbors[node]

    def degree(self, node: str) -> int:
        if node not in self._degree:
            self._degree[node] = int(self.conn.execute(
                "SELECT (SELECT COUNT(*) FROM relationships WHERE subject_id=?) + "
                "(SELECT COUNT(*) FROM relationships WHERE object_id=?)",
                (node, node),
            ).fetchone()[0])
        return self._degree[node]


def find_paths(conn: sqlite3.Connection, a: str, b: str, *, k: int = 3, max_depth: int = 6,
               max_expansions: int = 20000, exclude_predicates=()) -> list[dict]:
    """k cheapest simple paths. Cost = basis/confidence per edge + HUB_ALPHA*ln(1+degree) per intermediate node."""
    if a == b:
        return []
    graph = GraphView(conn, exclude_predicates)
    tie = itertools.count()
    heap: list = [(0.0, next(tie), (a,), ())]
    pops: dict[str, int] = defaultdict(int)
    found: list[tuple[float, tuple, tuple]] = []
    seen_sequences: set[tuple] = set()
    expansions = 0
    while heap and len(found) < k and expansions < max_expansions:
        cost, _, nodes, edges = heapq.heappop(heap)
        last = nodes[-1]
        if last == b:
            if nodes not in seen_sequences:
                seen_sequences.add(nodes)
                found.append((cost, nodes, edges))
            continue
        pops[last] += 1
        if pops[last] > k or len(edges) >= max_depth:
            continue
        expansions += 1
        for edge in graph.neighbors(last):
            nxt = edge["object_id"] if edge["subject_id"] == last else edge["subject_id"]
            if nxt in nodes:
                continue
            step = edge_cost(edge) + (0.0 if nxt == b else HUB_ALPHA * math.log1p(graph.degree(nxt)))
            heapq.heappush(heap, (cost + step, next(tie), nodes + (nxt,), edges + (edge,)))
    summaries = entity_summaries(conn, {n for _, nodes, _ in found for n in nodes})
    paths = []
    for cost, nodes, edges in found:
        steps = []
        for i, edge in enumerate(edges):
            forward = edge["subject_id"] == nodes[i]
            steps.append({**edge, "from": nodes[i], "to": nodes[i + 1], "forward": forward,
                          "label": edge_label(edge["predicate"], forward)})
        paths.append({
            "cost": round(cost, 3),
            "hops": len(edges),
            "nodes": [summaries.get(n, {"id": n, "name": n}) for n in nodes],
            "edges": steps,
            "via_hubs": [n for n in nodes[1:-1] if graph.degree(n) >= HUB_DEGREE],
            "bases": sorted({e["basis"] for e in edges}),
        })
    return paths


# (predicate, end that is the PARENT/OWNER)
OWNERSHIP_PREDICATES = {"direct_parent": "object", "owns": "subject"}


def ownership_chain(conn: sqlite3.Connection, entity_id: str, direction: str = "up", *, max_depth: int = 8,
                    max_nodes: int = 300, follow_same_as: bool = True) -> dict:
    """BFS over consolidation/ownership edges. up = parents and owners; down = subsidiaries and assets.
    `same_as` links are followed so a GLEIF chain continues into OpenSanctions ownership records."""
    if direction not in ("up", "down"):
        raise ValueError("direction must be up or down")
    nodes = [{"id": entity_id, "depth": 0, "from": None, "edge": None}]
    seen = {entity_id}
    frontier = [entity_id]
    depth = 0
    truncated = False
    while frontier and depth < max_depth:
        depth += 1
        nxt: list[str] = []
        for node in frontier:
            rows = conn.execute(
                f"SELECT {EDGE_COLUMNS} FROM relationships WHERE (subject_id=? OR object_id=?) "
                "AND predicate IN ('direct_parent','owns','same_as')",
                (node, node),
            ).fetchall()
            for r in rows:
                edge = dict(r)
                pred = edge["predicate"]
                if pred == "same_as":
                    if not follow_same_as:
                        continue
                    other = edge["object_id"] if edge["subject_id"] == node else edge["subject_id"]
                else:
                    parent_end = OWNERSHIP_PREDICATES[pred]
                    parent = edge["object_id"] if parent_end == "object" else edge["subject_id"]
                    child = edge["subject_id"] if parent_end == "object" else edge["object_id"]
                    if direction == "up" and child == node:
                        other = parent
                    elif direction == "down" and parent == node:
                        other = child
                    else:
                        continue
                if other in seen:
                    continue
                if len(seen) >= max_nodes:
                    truncated = True
                    break
                seen.add(other)
                forward = edge["subject_id"] == node
                nodes.append({"id": other, "depth": depth, "from": node,
                              "edge": {**edge, "forward": forward, "label": edge_label(pred, forward)}})
                nxt.append(other)
        frontier = nxt
    summaries = entity_summaries(conn, seen)
    for item in nodes:
        item["entity"] = summaries.get(item["id"], {"id": item["id"], "name": item["id"]})
    return {"root": entity_id, "direction": direction, "nodes": nodes, "truncated": truncated}
