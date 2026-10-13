import dataclasses
import os

# Set before app.settings loads backend/.env (setdefault), so a local crawler config never reaches the test suite.
os.environ["CRAWLER_URL"] = ""

import pytest

from app import db, demo
from app.ai import manager as manager_mod
from app.ai.providers import FakeProvider
from app.connectors import crawler as crawler_mod
from app.connectors import mca_upload as mca_upload_mod
from app.settings import settings


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.001)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def conn(db_path):
    c = db.connect(db_path)
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture
def seeded(conn):
    demo.seed(conn)
    return conn


@pytest.fixture
def make_manager(db_path):
    def build(provider=None, **overrides):
        cfg = dataclasses.replace(settings, db_path=db_path, ai_provider="fake", **overrides)
        clock = FakeClock()
        mgr = manager_mod.AIManager(cfg, provider or FakeProvider(), db_path=db_path, clock=clock.time,
                                    sleep=clock.sleep, poll_interval=0.25)
        mgr.fake_clock = clock
        return mgr
    return build


@pytest.fixture
def client(seeded, db_path, make_manager):
    from fastapi.testclient import TestClient

    from app.api import app, get_conn

    def _conn():
        c = db.connect(db_path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_conn] = _conn
    mgr = make_manager()
    manager_mod.set_manager(mgr)
    crawler_mod.set_client(crawler_mod.CrawlerClient(db_path=db_path))
    mca_upload_mod.db_path_override = db_path
    yield TestClient(app)
    mca_upload_mod.db_path_override = None
    app.dependency_overrides.clear()
    manager_mod.set_manager(None)
    crawler_mod.set_client(None)
