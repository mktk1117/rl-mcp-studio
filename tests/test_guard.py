"""Who may use this studio at all (`guard.py`).

The conftest gives every test client the bearer token, so the thousand
tests written before the guard existed still reach their routes. These
tests take it away again and ask what a stranger gets, what the page gives
a browser, and what the two checks refuse.
"""

from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

from rlmcp_studio import guard as guard_module
from rlmcp_studio.app import create_app
from rlmcp_studio.config import resolve


@pytest.fixture
def stranger(app):
  """A client that knows nothing: no cookie, no bearer."""
  with TestClient(app) as running:
    running.headers.pop("authorization", None)
    yield running


def test_a_stranger_is_refused_at_every_api_route_with_one_sentence(stranger):
  for path in ("/api/config", "/api/runs", "/api/tasks", "/api/doctor"):
    answer = stranger.get(path)
    assert answer.status_code == 401, path
    assert "credential" in answer.json()["error"]
  assert stranger.post("/api/build/launch", json={}).status_code == 401


def test_the_page_and_its_files_are_open_and_the_page_hands_out_the_cookie(stranger, app):
  page = stranger.get("/")
  assert page.status_code == 200
  # Named per port: two studios on one host must not share a cookie (guard.py).
  assert app.state.guard.cookie_name == f"{guard_module.COOKIE}_{app.state.guard.port}"
  assert page.cookies.get(guard_module.COOKIE) is None
  cookie = page.cookies.get(app.state.guard.cookie_name)
  assert cookie == app.state.guard.token
  raw = page.headers["set-cookie"].lower()
  assert "httponly" in raw and "samesite=strict" in raw and "path=/" in raw
  assert stranger.get("/static/js/main.js").status_code == 200


def test_a_browser_that_has_the_page_gets_in_by_cookie_alone(app):
  with TestClient(app) as browser:
    browser.headers.pop("authorization", None)
    browser.get("/")  # the cookie jar keeps what the page set
    assert browser.get("/api/config").status_code == 200
    # A state-changing request from a browser carries Origin; this studio's
    # own is allowed, a stranger's is not, and none at all is not either
    # with only a cookie to vouch for it.
    own = f"http://{app.state.guard._origin.host}:{app.state.guard._origin.port}"
    ok = browser.post("/api/settings", json={}, headers={"origin": own})
    assert ok.status_code != 401
    bad = browser.post("/api/settings", json={}, headers={"origin": "http://evil.example"})
    assert bad.status_code == 401
    none = browser.post("/api/settings", json={})
    assert none.status_code == 401


def test_a_bearer_token_gets_in_with_no_origin_and_a_wrong_one_does_not(app):
  with TestClient(app) as tool:
    tool.headers["authorization"] = f"Bearer {app.state.guard.token}"
    assert tool.get("/api/config").status_code == 200
    assert tool.post("/api/settings", json={}).status_code != 401
    tool.headers["authorization"] = "Bearer nope"
    assert tool.get("/api/config").status_code == 401


def test_the_live_feed_handshake_is_guarded_the_same_way(app):
  with TestClient(app) as browser:
    browser.headers.pop("authorization", None)
    with pytest.raises(Exception):
      with browser.websocket_connect("/ws"):
        pass
    browser.get("/")
    own = f"http://{app.state.guard._origin.host}:{app.state.guard._origin.port}"
    with browser.websocket_connect("/ws", headers={"origin": own}) as socket:
      hello = socket.receive_json()
      assert hello["kind"] == "hello"


def test_the_token_comes_from_the_environment_or_the_flag_and_is_left_for_tools(tmp_path, monkeypatch):
  monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
  monkeypatch.setenv("RLMCP_STUDIO_TOKEN", "from-env")
  assert resolve(records=[str(tmp_path / "r")], settings=str(tmp_path / "s.json")).token == "from-env"
  assert resolve(records=[str(tmp_path / "r")], settings=str(tmp_path / "s.json"),
                 token="from-flag").token == "from-flag"
  monkeypatch.delenv("RLMCP_STUDIO_TOKEN")
  app = create_app(resolve(records=[str(tmp_path / "r")], settings=str(tmp_path / "s.json")))
  minted = app.state.guard.token
  assert len(minted) >= 32 and not app.state.guard.from_env
  written = tmp_path / "state" / "rlmcp-studio" / "token"
  assert written.read_text().strip() == minted
  assert oct(written.stat().st_mode & 0o777) == "0o600", "nobody else on the machine"
  assert guard_module.read_token_file() == minted


def test_the_cli_recognises_a_running_studio_it_lacks_the_token_for(monkeypatch):
  """`rlmcp-studio` started twice must say 'already running', not bind again.

  A 401 is an answer from a studio, so it counts as one being there."""
  import urllib.error
  from rlmcp_studio import __main__ as cli

  class Refused(urllib.error.HTTPError):
    def __init__(self):
      super().__init__("http://x/api/config", 401, "no", {}, None)

  def urlopen(request, timeout=0):
    raise Refused()

  monkeypatch.setattr(cli.urllib.request, "urlopen", urlopen)
  monkeypatch.setenv("RLMCP_STUDIO_TOKEN", "")
  assert cli.already_serving("127.0.0.1", 8730) == "http://127.0.0.1:8730"


def test_the_token_survives_a_restart_so_open_tabs_keep_working(tmp_path, monkeypatch):
  """A token minted per process would log every tab out on every restart: the
  cookie would hold the previous token, every fetch would get 401, and the page
  would reload itself mid-sentence. The next process of the same port picks the file back up."""
  monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
  monkeypatch.delenv("RLMCP_STUDIO_TOKEN", raising=False)
  first = guard_module.AppGuard(port=8730)
  assert not first.reused
  second = guard_module.AppGuard(port=8730)
  assert second.token == first.token and second.reused
  # A flag or the environment still wins, and says so.
  third = guard_module.AppGuard(port=8730, token="chosen")
  assert third.token == "chosen" and not third.reused


def test_another_port_keeps_its_own_token_file_and_cookie(tmp_path, monkeypatch):
  """Two studios on one machine wrote one token file, so the tools -- and the
  next restart -- held whichever wrote last. And they set one cookie."""
  monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
  monkeypatch.delenv("RLMCP_STUDIO_TOKEN", raising=False)
  main = guard_module.AppGuard(port=8730)
  scratch = guard_module.AppGuard(port=8731)
  assert main.token != scratch.token
  assert guard_module.token_file(8730).name == "token"        # what tools read
  assert guard_module.token_file(8731).name == "token-8731"
  assert guard_module.read_token_file(8730) == main.token
  assert guard_module.read_token_file(8731) == scratch.token
  assert main.cookie()["key"] != scratch.cookie()["key"]
  # Each guard reads only its own cookie, so the other studio's cannot vouch.
  assert main.presented({}, {scratch.cookie_name: scratch.token}) is None
  assert main.presented({}, {main.cookie_name: main.token}) == main.token
