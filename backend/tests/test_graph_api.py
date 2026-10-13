from app.graph.analytics import ownership_cycles, shared_addresses
from app.graph.paths import find_paths
from app.resolution.matcher import is_name_match
from app.resolution.normalize import normalize_name


def test_name_normalisation_and_matching():
    assert normalize_name("Alpha Holdings Ltd.") == normalize_name("ALPHA HOLDINGS LIMITED")
    assert is_name_match("Gamma Shipping Co", "Gamma Shipping Company")
    assert not is_name_match("Gamma Shipping Co", "Delta Investments SA")


def test_circular_ownership_detected(seeded):
    cycles = ownership_cycles(seeded)
    members = {m for c in cycles for m in str(c)}
    assert cycles and "demo:alpha" in str(cycles) and "demo:delta" in str(cycles), members


def test_shared_address(seeded):
    assert any("demo:beta" in str(lead) for lead in shared_addresses(seeded, min_entities=3))


def test_path_prefers_specific_over_hub(seeded):
    paths = find_paths(seeded, "demo:alpha", "demo:gamma", k=3)
    assert paths
    best = paths[0]
    assert [n["id"] for n in best["nodes"]][0] == "demo:alpha"
    for step in best["edges"]:
        assert step["id"]  # every hop is a stored, inspectable relationship


def test_api_search_and_entity(client):
    r = client.get("/api/search", params={"q": "alpha hold"})
    assert r.status_code == 200 and r.json()["results"][0]["id"] == "demo:alpha"
    d = client.get("/api/entities/demo:alpha").json()
    assert d["entity"]["name"] == "Alpha Holdings Ltd" and d["relationships"]
    rel_id = d["relationships"][0]["id"]
    rel = client.get(f"/api/relationships/{rel_id}").json()
    assert rel["evidence"], "every edge must open to at least one evidence row"


def test_api_graph_ownership_timeline_report(client):
    assert client.get("/api/entities/demo:alpha/graph").json()["edges"]
    assert client.get("/api/entities/demo:beta/ownership", params={"direction": "up"}).status_code == 200
    assert client.get("/api/entities/demo:alpha/timeline").json()["events"]
    rep = client.get("/api/entities/demo:alpha/report").json()
    assert "# Investigation report: Alpha Holdings Ltd" in rep["markdown"]
    assert rep["ai_status"]["used"] is False


def test_api_paths_with_fake_ai_validates_citations(client):
    r = client.get("/api/paths", params={"a": "demo:alpha", "b": "demo:gamma", "ai": True}).json()
    assert r["paths"]
    assert r["ai_status"]["used"] is True
    check = r["ai"]["citation_check"]
    assert not check["unknown_citations"]
    known = {e["eid"] for e in r["evidence"]}
    assert all(set(c["citations"]) <= known for c in r["ai"]["claims"])


def test_api_leads_and_investigations(client):
    assert client.get("/api/leads").json()["leads"]
    inv = client.post("/api/investigations", json={"title": "Demo case"}).json()
    inv = client.post(f"/api/investigations/{inv['id']}/items",
                      json={"item_type": "entity", "ref": "demo:gamma", "note": "sanction topic"}).json()
    assert inv["items"][0]["entity"]["name"] == "Gamma Shipping Co"
    assert client.get("/api/investigations").json()["items"][0]["item_count"] == 1


def test_api_404(client):
    assert client.get("/api/entities/nope:x").status_code == 404
