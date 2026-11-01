import os
os.environ["TESTER_TOKEN"] = "t0k"
for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "XAI_API_KEY"): os.environ.pop(k, None)
import app as A

def geo(cc, rc): return lambda ip, trace=None: {"country_code": cc, "region_code": rc, "source": "test"}

def client(monkeypatch, cc="US", rc="CA"):
    monkeypatch.setattr(A, "geo_lookup", geo(cc, rc)); A._hits.clear(); return A.app.test_client()

def test_us_allowed(monkeypatch):
    r = client(monkeypatch).get("/"); assert r.status_code == 200 and b"TEST MODE" in r.data and b"noindex" in r.data

def test_blocked_foreign_and_states(monkeypatch):
    assert client(monkeypatch, "CA", "ON").get("/").status_code == 451
    for s in ["NY", "PA", "MA", "MD", "VA"]: assert client(monkeypatch, "US", s).get("/").status_code == 451

def test_fail_closed(monkeypatch):
    monkeypatch.setattr(A, "geo_lookup", lambda ip, trace=None: None); assert A.app.test_client().get("/").status_code == 451

def test_tester_bypass_and_debug(monkeypatch):
    c = client(monkeypatch, "US", "NY")
    assert c.get("/?tester=wrong").status_code == 403
    assert c.get("/?tester=t0k").status_code == 302
    assert c.get("/").status_code == 200
    assert c.get("/debug/geo?ip=1.2.3.4").status_code == 451
    assert client(monkeypatch).get("/debug/geo?ip=1.2.3.4").status_code == 403

def test_reading_requires_consent_and_mock(monkeypatch):
    c = client(monkeypatch)
    assert c.post("/api/reading", json={}).status_code == 400
    j = c.post("/api/reading", json={"age_ok": 1, "fun_ok": 1, "signals": {"hour": 23}}).get_json()
    assert j["mode"] == "mock" and j["image"]["src"].startswith("/static/img/") and j["used"]["template"]["theme"] in ("stars", "moon")

def test_blocklist():
    assert A.safe("You will find love and money")[1] is True
    assert A.safe("A teapot-shaped cloud appears.")[1] is False

def test_rate_limit(monkeypatch):
    c = client(monkeypatch)
    codes = [c.post("/api/reading", json={"age_ok": 1, "fun_ok": 1}).status_code for _ in range(A.RATE_LIMIT + 1)]
    assert codes[-1] == 429
