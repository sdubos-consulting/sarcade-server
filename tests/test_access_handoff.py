import base64
import json

from fastapi.testclient import TestClient

from sarcade.api.app import app

client = TestClient(app)


def jwt(claims: dict) -> str:
    def part(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{part({'alg': 'RS256'})}.{part(claims)}.c2lnbmF0dXJl"


def test_handoff_without_access_says_no_login_needed():
    r = client.get("/api/v0.1/access/handoff")
    assert r.status_code == 200
    assert "pas derrière Cloudflare Access" in r.text
    assert "SarcadeAccess" not in r.text
    assert r.headers["cache-control"].startswith("no-store")


def test_handoff_hands_the_cookie_token_to_the_application():
    token = jwt({"email": "stephane@example.org", "exp": 1893456000})
    r = client.get("/api/v0.1/access/handoff", cookies={"CF_Authorization": token})
    assert r.status_code == 200
    assert "stephane@example.org" in r.text
    assert json.dumps(token) in r.text
    assert "window.SarcadeAccess.postMessage" in r.text
    assert "no-store" in r.headers["cache-control"]


def test_handoff_uses_the_header_when_there_is_no_cookie():
    token = jwt({"email": "a@b.fr"})
    r = client.get("/api/v0.1/access/handoff", headers={"Cf-Access-Jwt-Assertion": token})
    assert json.dumps(token) in r.text


def test_handoff_refuses_a_token_that_is_not_a_jwt():
    r = client.get("/api/v0.1/access/handoff", headers={"Cf-Access-Jwt-Assertion": "a.b.c</script><script>alert(1)"})
    assert "pas derrière Cloudflare Access" in r.text
    assert "alert(1)" not in r.text


def test_session_reports_email_and_expiry_but_never_the_token():
    token = jwt({"email": "a@b.fr", "exp": 1893456000})
    j = client.get("/api/v0.1/access/session", headers={"Cf-Access-Jwt-Assertion": token}).json()
    assert j["protected"] is True and j["email"] == "a@b.fr" and j["expires_at"].startswith("2030-01-01")
    assert token not in json.dumps(j)
    assert client.get("/api/v0.1/access/session").json() == {"protected": False}
