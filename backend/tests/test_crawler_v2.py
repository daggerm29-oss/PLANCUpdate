"""Crawler v2 (services/crawler/app.py) source policy and PLANC claim handling. No network, no Serper credits."""
import importlib.util
import json
from pathlib import Path

import pytest
import requests

from app import db
from app.connectors import mca
from app.intel.claims import build_claims, store_claims, verify_claims
from app.intel.parse import parse_response

from .test_integration import FakeResp, MCA_HEADER, MCA_ROW

pytest.importorskip("flask")
pytest.importorskip("bs4")

CRAWLER_PATH = Path(__file__).resolve().parents[2] / "services" / "crawler" / "app.py"


@pytest.fixture(scope="module")
def crawler():
    spec = importlib.util.spec_from_file_location("crawler_app", CRAWLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Page:
    def __init__(self, status=200, html="", url="", headers=None):
        self.status_code, self.text, self.url, self.headers = status, html, url, headers or {}

    def json(self):
        return json.loads(self.text)


WIKI_HTML = """<html><body><table class="infobox">
<tr><th>Type</th><td>Public</td></tr>
<tr><th>Traded as</th><td>NYSE: WAB<sup>[1]</sup></td></tr>
<tr><th>Headquarters</th><td>Pittsburgh, Pennsylvania, U.S.</td></tr>
<tr><th>Revenue</th><td>US$10.39 billion (2024)</td></tr>
<tr><th>Number of employees</th><td>29,000</td></tr>
<tr><th>Subsidiaries</th><td><ul><li>Faiveley Transport</li><li>Wabtec India Industrial Private Limited</li></ul></td></tr>
<tr><th>Key people</th><td>Rafael Santana (CEO)</td></tr>
</table><h2>Mergers and acquisitions</h2><p>In 2019 Wabtec acquired GE Transportation in a merger with GE.</p>
<h2>References</h2></body></html>"""
OFFICIAL_HTML = ("<html><head><meta name='description' content='Wabtec India official site'></head><body>"
                 + "<p>About us. " + "Rail technology. " * 120 + "</p>"
                 "<footer>CIN: U74999KA1999PTC143212 Registered Office: Plot 12, Hosur Road, Bengaluru 560100</footer>"
                 "<script src='https://www.google.com/recaptcha/api.js'></script></body></html>")
CAPTCHA_HTML = "<html><body><h1>Verify you are human</h1><div class='g-recaptcha'></div></body></html>"


def run(crawler, monkeypatch, search, pages, registry_search=None):
    gets, posts = [], []

    def fake_get(url, **kw):
        gets.append(url)
        item = pages.get(url)
        if item is None:
            raise AssertionError(f"unexpected GET {url}")
        if isinstance(item, Exception):
            raise item
        return item

    def fake_post(url, json=None, **kw):
        posts.append((url, json))
        if url == "https://google.serper.dev/search":
            body = search if len([p for p in posts if p[0] == url]) == 1 else (registry_search or {"organic": []})
            return Page(200, __import__("json").dumps(body))
        if url == "https://scrape.serper.dev":
            return Page(200, __import__("json").dumps({"text": "Gateway text. CIN: U74999KA1999PTC143212 " + "x" * 2000}))
        raise AssertionError(url)

    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    monkeypatch.setattr(crawler.requests, "get", fake_get)
    monkeypatch.setattr(crawler.requests, "post", fake_post)
    resp = crawler.app.test_client().post("/google_and_deep_scrape", json={"query": "Wabtec Corporation"})
    return resp.status_code, resp.get_json(), gets, posts


SEARCH = {
    "knowledgeGraph": {"title": "Wabtec", "type": "Company", "website": "https://www.wabtec.co.in/",
                       "description": "Wabtec Corporation is an American company.",
                       "descriptionLink": "https://en.wikipedia.org/wiki/Wabtec",
                       "attributes": {"Headquarters": "Pittsburgh, PA", "Parent organization": "Westinghouse Air Brake"}},
    "organic": [
        {"title": "Wabtec - Wikipedia", "link": "https://en.wikipedia.org/wiki/Wabtec", "snippet": "..."},
        {"title": "Wabtec Corporation | LinkedIn", "link": "https://www.linkedin.com/company/wabtec",
         "snippet": "Wabtec | 380,512 followers on LinkedIn. Industry: Transportation Equipment. 10,001+ employees."},
        {"title": "Wabtec India", "link": "https://www.wabtec.co.in/", "snippet": "..."},
    ],
}


def test_linkedin_is_never_fetched_and_snippet_fields_are_attributed(crawler, monkeypatch):
    pages = {"https://en.wikipedia.org/wiki/Wabtec": Page(200, WIKI_HTML),
             "https://www.wabtec.co.in/": Page(200, OFFICIAL_HTML)}
    status, body, gets, posts = run(crawler, monkeypatch, SEARCH, pages)
    assert status == 200, body
    assert not any("linkedin.com" in u for u in gets)
    assert not any(u == "https://scrape.serper.dev" for u, _ in posts)
    assert body["serper_calls"] == {"search": 2, "scrape_gateway": 0}
    li = [f for f in body["fields"] if f["source_type"] == "linkedin_search_result"]
    assert {f["field"] for f in li} == {"linkedin_url", "workforce", "industry"}
    assert all(f["source_url"] == "https://www.linkedin.com/company/wabtec" and f["retrieved_at"] for f in li)
    for f in body["fields"]:
        assert f["source_url"] and f["source_type"] and f["method"] and f["retrieved_at"]


def test_wikipedia_infobox_and_official_site_fields(crawler, monkeypatch):
    pages = {"https://en.wikipedia.org/wiki/Wabtec": Page(200, WIKI_HTML),
             "https://www.wabtec.co.in/": Page(200, OFFICIAL_HTML)}
    _, body, _, _ = run(crawler, monkeypatch, SEARCH, pages)
    by = {}
    for f in body["fields"]:
        by.setdefault((f["field"], f["source_type"]), []).append(f["value"])
    assert by[("stock_ticker", "wikipedia")] == ["NYSE: WAB"]
    assert by[("workforce", "wikipedia")] == ["29,000"]
    assert by[("subsidiary_mention", "wikipedia")] == ["Faiveley Transport", "Wabtec India Industrial Private Limited"]
    assert by[("key_people_mention", "wikipedia")] == ["Rafael Santana (CEO)"]
    assert ("director_name", "wikipedia") not in by  # officers are never reported as directors
    assert by[("parent_company_mention", "search_knowledge_graph")] == ["Westinghouse Air Brake"]
    assert by[("cin", "official_site")] == ["U74999KA1999PTC143212"]
    assert by[("registered_office", "official_site")][0].startswith("Plot 12, Hosur Road")
    assert by[("acquisition", "wikipedia")]
    # a reCAPTCHA script on a long, real page is not treated as a block
    assert next(e for e in body["fetch_log"] if e["kind"] == "official_site")["outcome"] == "fetched"


def test_access_restrictions_are_not_bypassed(crawler, monkeypatch):
    search = {"organic": [
        {"title": "Wabtec | LinkedIn", "link": "https://in.linkedin.com/company/wabtec", "snippet": "5,001-10,000 employees"},
        {"title": "Wabtec India", "link": "https://www.wabtec.co.in/", "snippet": "Rail"},
        {"title": "Wabtec - Zauba", "link": "https://www.zaubacorp.com/company/WABTEC/U74999KA1999PTC143212"},
    ]}
    pages = {"https://www.wabtec.co.in/": Page(200, CAPTCHA_HTML),
             "https://www.zaubacorp.com/company/WABTEC/U74999KA1999PTC143212": Page(403, "Forbidden")}
    status, body, gets, posts = run(crawler, monkeypatch, search, pages)
    assert status == 200
    assert not any(u == "https://scrape.serper.dev" for u, _ in posts)
    log = {e["kind"]: e for e in body["fetch_log"]}
    assert log["official_site"]["outcome"] == "blocked" and "not retried" in log["official_site"]["reason"]
    assert log["registry_aggregator"]["outcome"] == "blocked"
    # it continues with what the search results permit
    assert {f["field"] for f in body["fields"]} >= {"description", "linkedin_url", "workforce"}
    md = body["company_metadata"]
    assert "Active" not in md and "Disclosed" not in md
    assert body["directors_network"] == []


def test_login_redirect_is_blocked(crawler, monkeypatch):
    search = {"organic": [{"title": "Acme", "link": "https://acme.example/", "snippet": "Acme"}]}
    pages = {"https://acme.example/": Page(200, "<html>" + "x" * 5000 + "</html>", url="https://acme.example/login?next=/")}
    _, body, _, posts = run(crawler, monkeypatch, search, pages)
    assert body["fetch_log"][0]["outcome"] == "blocked"
    assert not any(u == "https://scrape.serper.dev" for u, _ in posts)


def test_gateway_only_for_technical_failures(crawler, monkeypatch):
    search = {"organic": [{"title": "Acme", "link": "https://acme.example/", "snippet": "Acme"}]}
    pages = {"https://acme.example/": requests.ConnectTimeout("timeout")}
    _, body, _, posts = run(crawler, monkeypatch, search, pages)
    entry = body["fetch_log"][0]
    assert entry["outcome"] == "fetched" and entry["via"] == "serper_scrape_gateway"
    assert body["serper_calls"]["scrape_gateway"] == 1
    assert any(f["field"] == "cin" and f["method"] == "labelled_cin" for f in body["fields"])


def test_registry_directors_and_co_listed_are_separate(crawler, monkeypatch):
    html = ("<html><body><p>CIN: U74999KA1999PTC143212</p><p>Authorized Capital: ₹ 5,00,00,000</p>"
            "<p>Directors of WABTEC INDIA are Asha Rao, Vikram Iyer and Meena Das.</p>"
            "<p>Other companies: U12345MH2001PTC123456 SOME OTHER PRIVATE LIMITED</p>" + "<p>filler</p>" * 300
            + "</body></html>")
    search = {"organic": [{"title": "Wabtec - Zauba", "link": "https://www.zaubacorp.com/company/W/X", "snippet": "."}]}
    _, body, _, _ = run(crawler, monkeypatch, search, {"https://www.zaubacorp.com/company/W/X": Page(200, html)})
    fields = {(f["field"], f["value"]) for f in body["fields"]}
    assert ("cin", "U74999KA1999PTC143212") in fields
    assert ("co_listed_identifier", "U12345MH2001PTC123456") in fields
    assert ("co_listed_identifier", "U74999KA1999PTC143212") not in fields
    assert {v for f, v in fields if f == "director_name"} == {"Asha Rao", "Vikram Iyer", "Meena Das"}
    assert [d["director_name"] for d in body["directors_network"]] == ["Asha Rao", "Vikram Iyer", "Meena Das"]


# ------------------------------------------------------------------ PLANC side

def _v2_response(crawler, monkeypatch):
    pages = {"https://en.wikipedia.org/wiki/Wabtec": Page(200, WIKI_HTML),
             "https://www.wabtec.co.in/": Page(200, OFFICIAL_HTML)}
    return run(crawler, monkeypatch, SEARCH, pages)[1]


def test_v2_claims_keep_provenance_and_stay_unverified(conn, crawler, monkeypatch, tmp_path):
    body = _v2_response(crawler, monkeypatch)
    parsed = parse_response(body)
    built = build_claims(parsed)
    assert built and all(c["source_url"] and c["retrieved_at"] and 0 < c["confidence"] <= 0.6 for c in built)
    types = {c["field"]: c["claim_type"] for c in built}
    assert types["subsidiary_mention"] == types["parent_company_mention"] == "relationship_mention"
    assert types["key_people_mention"] == "officer_mention"
    assert types["linkedin_url"] == "profile_link"
    with db.transaction(conn):
        ids = store_claims(conn, subject_entity_id=None, subject_query="Wabtec Corporation", claims=built,
                           raw_record_id=None)
        check = verify_claims(conn, ids, "Wabtec Corporation", None)
    assert check["counts"].get("verified", 0) == 0
    stored = db.rows(conn.execute("SELECT retrieved_at FROM claims"))
    assert {r["retrieved_at"] for r in stored} <= {c["retrieved_at"] for c in built}
    assert conn.execute("SELECT COUNT(*) FROM relationships WHERE source_id='crawler'").fetchone()[0] == 0


def test_official_site_cin_verifies_only_through_registry(conn, crawler, monkeypatch, tmp_path):
    f = tmp_path / "mca.csv"
    f.write_text(MCA_HEADER + MCA_ROW, encoding="utf-8")
    mca.load(conn, f)
    body = _v2_response(crawler, monkeypatch)
    built = build_claims(parse_response(body))
    with db.transaction(conn):
        ids = store_claims(conn, subject_entity_id=None, subject_query="Wabtec Corporation", claims=built,
                           raw_record_id=None)
        verify_claims(conn, ids, "Wabtec Corporation", None)
    cin = db.rows(conn.execute("SELECT status FROM claims WHERE field='cin'"))
    # the CIN belongs to Wabtec India, not Wabtec Corporation: a possible subsidiary, never merged
    assert cin and all(r["status"] == "related" for r in cin)


def test_subsidiary_mention_verified_only_by_registry_relationship(conn):
    for eid, name in (("gleif:PARENT", "Wabtec Corporation"), ("gleif:CHILD", "Faiveley Transport")):
        db.upsert_entity(conn, {"id": eid, "source_id": "gleif", "name": name})
    claim = {"claim_type": "relationship_mention", "field": "subsidiary_mention", "value": "Faiveley Transport",
             "source_url": "https://en.wikipedia.org/wiki/Wabtec", "evidence_excerpt": "Subsidiaries: Faiveley Transport",
             "extraction_method": "crawler:wikipedia:infobox[subsidiaries]", "confidence": 0.4}
    with db.transaction(conn):
        ids = store_claims(conn, subject_entity_id="gleif:PARENT", subject_query="Wabtec Corporation",
                           claims=[claim], raw_record_id=None)
        verify_claims(conn, ids, "Wabtec Corporation", "gleif:PARENT")
    assert conn.execute("SELECT status FROM claims WHERE id=?", ids).fetchone()[0] == "unverified"

    with db.transaction(conn):
        db.upsert_relationship(conn, {"source_id": "gleif", "subject_id": "gleif:CHILD", "predicate": "direct_parent",
                                      "object_id": "gleif:PARENT"})
        verify_claims(conn, ids, "Wabtec Corporation", "gleif:PARENT")
    status, verification = conn.execute("SELECT status, verification FROM claims WHERE id=?", ids).fetchone()
    assert status == "verified" and json.loads(verification)["predicate"] == "direct_parent"


def test_reported_credits(crawler):
    from app.connectors.crawler import reported_credits
    assert reported_credits({"serper_calls": {"search": 2, "scrape_gateway": 1}}) == 4
    assert reported_credits({}) is None
