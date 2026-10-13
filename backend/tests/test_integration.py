"""Integration tests: real sample importers, crawler client limits (no network, no Serper credits), claim verification."""
import dataclasses
import json
import threading
from pathlib import Path

import pytest
import requests

from app import db
from app.connectors import mca, samples
from app.connectors.crawler import CrawlerClient, CrawlerError
from app.intel.service import investigate
from app.settings import settings

from .conftest import FakeClock

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples.zip"
WABTEC = json.loads((Path(__file__).parent / "fixtures_crawler_wabtec.json").read_text())

MCA_HEADER = ("CIN,Company Name,Company Registration Date,Company Category,Company Class,Listing Status,Authorized Capital,"
              "Paidup Capital,Company ROC,Company Address,Pin Code,Company State,Company Status,Company Sub Category,"
              "Company Industrial Classification\n")
MCA_ROW = ('U74999KA1999PTC143212,WABTEC INDIA INDUSTRIAL PRIVATE LIMITED,1999-01-01,Company limited by shares,Private,'
           'Unlisted,3520000000.00,491835300.00,ROC Bangalore,"Whitefield,Bangalore,Karnataka,560066-India", ,Karnataka,'
           'Active,Non-government company,Manufacturing\n')


class FakeResp:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = {"content-type": "application/json", **(headers or {})}
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = 0
        self.lock = threading.Lock()

    def post(self, url, **kw):
        with self.lock:
            self.posts += 1
            item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kw):
        return FakeResp(200, {"status": "healthy", "serper_configured": True})


@pytest.fixture
def make_crawler(db_path):
    def build(responses, **overrides):
        cfg = dataclasses.replace(settings, db_path=Path(db_path), crawler_url="http://crawler.test", **overrides)
        clock = FakeClock()
        session = FakeSession(responses)
        client = CrawlerClient(cfg, db_path=db_path, session=session, clock=clock.time, sleep=clock.sleep,
                               poll_interval=0.01)
        client.fake_session = session
        client.fake_clock = clock
        return client
    return build


# ------------------------------------------------------------------ importers on the real samples

needs_samples = pytest.mark.skipif(not SAMPLES.exists(), reason="samples.zip not present")


@pytest.fixture(scope="module")
def loaded(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("samples") / "s.db")
    c = db.connect(path)
    db.init_db(c)
    first = samples.load_all(c, SAMPLES)
    yield c, first
    c.close()


@needs_samples
def test_samples_load_and_idempotent(loaded):
    conn, first = loaded
    assert first["datasets_mca_karnataka.csv.sample.csv"]["new"] == 100
    assert first["gleif_level1_sample.xml"]["new"] == 20
    assert first["gleif_level2_sample.xml"]["relationships"] == 20
    assert first["datasets_nse_prs_ra_sebi.xls"]["format"] == "xls"
    assert first["datasets_nse_prs_ra_others.xls"]["format"] == "xlsx"
    schemes = {r[0] for r in conn.execute("SELECT DISTINCT scheme FROM identifiers")}
    assert {"cin", "lei", "pan"} <= schemes
    before = conn.execute("SELECT COUNT(*) FROM raw_records").fetchone()[0]
    second = samples.load_all(conn, SAMPLES)
    assert not any(v.get("new") for v in second.values())
    assert conn.execute("SELECT COUNT(*) FROM raw_records").fetchone()[0] == before


@needs_samples
def test_gleif_level2_keeps_child_to_parent_direction(loaded):
    import xml.etree.ElementTree as ET
    import zipfile

    conn, _ = loaded
    with zipfile.ZipFile(SAMPLES) as z:
        name = next(n for n in z.namelist() if n.endswith("gleif_level2_sample.xml"))
        root = ET.fromstring(z.read(name))
    expected = set()
    for rel in root.iter():
        if rel.tag.endswith("}Relationship"):
            start = next(e for e in rel.iter() if e.tag.endswith("StartNode"))
            end = next(e for e in rel.iter() if e.tag.endswith("EndNode"))
            nid = lambda n: next(e.text for e in n if e.tag.endswith("NodeID"))
            expected.add((f"gleif:{nid(start)}", f"gleif:{nid(end)}"))
    stored = {(r[0], r[1]) for r in conn.execute(
        "SELECT subject_id, object_id FROM relationships WHERE source_id='gleif' "
        "AND predicate IN ('direct_parent','ultimate_parent')")}
    assert expected and expected == stored  # subject = child (StartNode), object = parent (EndNode)


@needs_samples
def test_search_by_cin_and_pan(loaded):
    conn, _ = loaded
    cin = conn.execute("SELECT value FROM identifiers WHERE scheme='cin' AND entity_id LIKE 'mca:%' LIMIT 1").fetchone()[0]
    out = investigate(conn, cin.lower())
    assert out["subject_id"] and out["detected_identifiers"][0]["scheme"] == "cin"
    pan = conn.execute("SELECT value FROM identifiers WHERE scheme='pan' LIMIT 1").fetchone()[0]
    assert investigate(conn, pan)["subject_id"]


# ------------------------------------------------------------------ crawler client

def test_crawler_cache_avoids_second_call(make_crawler):
    c = make_crawler([FakeResp(200, WABTEC)])
    first = c.scrape("Wabtec  Corporation")
    second = c.scrape("wabtec corporation")
    assert first.status == "ok" and not first.cached
    assert second.cached and c.fake_session.posts == 1
    assert c.usage()["last_24h"]["upstream_attempts"] == 1


def test_crawler_does_not_retry_500(make_crawler):
    c = make_crawler([FakeResp(500, {"error": "boom"}), FakeResp(200, WABTEC)], crawler_max_retries=2)
    with pytest.raises(CrawlerError) as e:
        c.scrape("Acme")
    assert e.value.code == "upstream" and c.fake_session.posts == 1


def test_crawler_retries_503_bounded(make_crawler):
    c = make_crawler([FakeResp(503, {"error": "x"})], crawler_max_retries=2)
    with pytest.raises(CrawlerError):
        c.scrape("Acme")
    assert c.fake_session.posts == 3


def test_crawler_errors_not_cached(make_crawler):
    c = make_crawler([requests.ConnectionError("down"), FakeResp(200, WABTEC)], crawler_max_retries=0)
    with pytest.raises(CrawlerError) as e:
        c.scrape("Acme")
    assert e.value.code == "unreachable"
    assert c.scrape("Acme").status == "ok" and c.fake_session.posts == 2


def test_crawler_rpm_and_daily_budget(make_crawler):
    c = make_crawler([FakeResp(200, WABTEC)], crawler_rpm=2, crawler_daily_budget=3)
    c.scrape("a"); c.scrape("b")
    with pytest.raises(CrawlerError) as e:
        c.scrape("c")
    assert e.value.code == "rate_limited"
    c.fake_clock.now += 61
    c.scrape("c")
    c.fake_clock.now += 61
    with pytest.raises(CrawlerError) as e:
        c.scrape("d")
    assert e.value.code == "budget_exhausted" and c.fake_session.posts == 3


def test_crawler_concurrent_identical_requests_dedup(make_crawler):
    c = make_crawler([FakeResp(200, WABTEC)])
    results = []
    threads = [threading.Thread(target=lambda: results.append(c.scrape("Wabtec"))) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 5 and c.fake_session.posts == 1


def test_crawler_not_configured(db_path):
    cfg = dataclasses.replace(settings, db_path=Path(db_path), crawler_url="")
    with pytest.raises(CrawlerError) as e:
        CrawlerClient(cfg, db_path=db_path).scrape("x")
    assert e.value.code == "not_configured"


# ------------------------------------------------------------------ claims + verification

def test_subsidiary_cin_is_related_not_merged(conn, make_crawler, tmp_path):
    f = tmp_path / "mca.csv"
    f.write_text(MCA_HEADER + MCA_ROW, encoding="utf-8")
    mca.load(conn, f)
    c = make_crawler([FakeResp(200, WABTEC)])
    out = investigate(conn, "Wabtec Corporation", crawl=True, client=c)
    assert out["crawl"]["ok"], out["crawl"]
    claims = out.get("claims") or []
    cin = [x for x in claims if x["field"] == "cin"]
    assert cin and cin[0]["status"] == "related"
    assert all(x["source_url"] and x["extraction_method"] and x["retrieved_at"] for x in claims)
    assert not any(x["status"] == "verified" for x in claims)
    assert not any(x["value"] == "Key Managerial Personnel / Corporate Network" for x in claims)
    assert not any(x["field"] == "company_status" for x in claims)
    assert conn.execute("SELECT COUNT(*) FROM relationships WHERE source_id='crawler'").fetchone()[0] == 0


def test_matching_cin_and_name_is_verified(conn, make_crawler, tmp_path):
    f = tmp_path / "mca.csv"
    f.write_text(MCA_HEADER + MCA_ROW, encoding="utf-8")
    mca.load(conn, f)
    body = {**WABTEC, "target_query": "Wabtec India Industrial Private Limited"}
    c = make_crawler([FakeResp(200, body)])
    out = investigate(conn, "U74999KA1999PTC143212", crawl=True, client=c)
    assert out["subject_id"] == "mca:U74999KA1999PTC143212"
    verified = out["dossier"]["verified"]["claims"]
    assert any(v["field"] == "cin" for v in verified)


def test_api_intel_without_crawl_spends_nothing(client):
    r = client.post("/api/intel/investigate", json={"query": "anything"})
    assert r.status_code == 200 and r.json()["crawl"] is None
    assert client.get("/api/crawler/usage").json()["last_24h"]["upstream_attempts"] == 0
