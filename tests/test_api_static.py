from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from src.api.app import create_app
from tests.api_fakes import ORIGIN, Api


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>Inbox Agent</title>")
    (tmp_path / "assets" / "app-abc123.js").write_text("console.log('ok')")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    return tmp_path


def make_client(sf: sessionmaker[Session], web_dist: Path | None) -> TestClient:
    deps = Api(sf).deps
    app = create_app(trigger=lambda: None, token=None, api=deps, web_dist=web_dist)
    return TestClient(app, base_url="https://testserver", headers={"Origin": ORIGIN})


def test_root_and_spa_routes_serve_index_without_cache(
    session_factory: sessionmaker[Session], dist: Path
) -> None:
    client = make_client(session_factory, dist)
    for path in ("/", "/caixa/abc123", "/aprovacoes"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "Inbox Agent" in r.text
        assert r.headers["cache-control"] == "no-cache"


def test_hashed_assets_are_immutable_and_other_files_are_served(
    session_factory: sessionmaker[Session], dist: Path
) -> None:
    client = make_client(session_factory, dist)
    r = client.get("/assets/app-abc123.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get("/favicon.svg").status_code == 200


def test_unknown_api_path_stays_json_404(session_factory: sessionmaker[Session], dist: Path) -> None:
    client = make_client(session_factory, dist)
    r = client.get("/api/nao-existe")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_healthz_and_webhook_are_not_shadowed(session_factory: sessionmaker[Session], dist: Path) -> None:
    client = make_client(session_factory, dist)
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.post("/webhooks/gmail", json={"message": {"data": "%%%"}}).status_code == 204


@pytest.mark.parametrize(
    "path",
    ["/..%2f..%2fetc/passwd", "/%2e%2e/secrets/token.json", "/assets/..%2f..%2findex.html/../../x", "//etc/passwd"],
)
def test_path_traversal_never_leaves_dist(
    session_factory: sessionmaker[Session], dist: Path, tmp_path_factory: pytest.TempPathFactory, path: str
) -> None:
    secret = tmp_path_factory.mktemp("outside") / "secret.txt"
    secret.write_text("TOP-SECRET")
    client = make_client(session_factory, dist)
    r = client.get(path)
    assert "TOP-SECRET" not in r.text
    assert "root:" not in r.text
    assert r.status_code in (200, 404)  # 200 only ever for index.html


def test_nothing_is_mounted_without_a_build(session_factory: sessionmaker[Session], tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    for dist in (empty, None):
        client = make_client(session_factory, dist)
        assert client.get("/").status_code == 404


def test_security_headers_are_present_on_static_responses(
    session_factory: sessionmaker[Session], dist: Path
) -> None:
    client = make_client(session_factory, dist)
    h = client.get("/").headers
    assert h["x-frame-options"] == "DENY"
    assert "default-src 'self'" in h["content-security-policy"]
    assert "max-age=" in h["strict-transport-security"]
