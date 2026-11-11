"""Server-side check of the Cloudflare Access token."""
import os
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from starlette.websockets import WebSocketDisconnect

from sarcade.access import logbook as access_logbook
from sarcade.access.verify import (AccessConfig, AccessDenied, AccessIdentity, KeyStore, Verifier, load_config,
                                   normal_host, set_verifier)
from sarcade.api.app import app

TEAM = "sdubos.cloudflareaccess.com"
AUD = "aud-sarcade-0123456789"
HOST = "app.sarcade.org"


def new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk(key, kid):
    data = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    data.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return data


KEY, OTHER = new_key(), new_key()


def token(key=KEY, kid="k1", **over):
    now = int(time.time())
    claims = {"aud": [AUD], "iss": f"https://{TEAM}", "email": "operateur@example.org", "sub": "u-1",
              "iat": now, "exp": now + 3600, "type": "app"}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


class FakeCerts:
    def __init__(self, *keys):
        self.keys, self.calls = list(keys), 0

    def __call__(self, url):
        assert url == f"https://{TEAM}/cdn-cgi/access/certs"
        self.calls += 1
        return {"keys": self.keys}


def verifier(mode="enforce", certs=None, clock=None):
    config = AccessConfig(mode=mode, team_domain=TEAM, audiences=(AUD,), hosts=(HOST,))
    store = KeyStore(config.certs_url, fetch=certs or FakeCerts(jwk(KEY, "k1")), **({"clock": clock} if clock else {}))
    return Verifier(config, store)


@pytest.fixture
def use():
    def install(v):
        set_verifier(v)
        return TestClient(app)
    yield install
    set_verifier(None)


def public(tok=None):
    headers = {"host": HOST}
    if tok:
        headers["cf-access-jwt-assertion"] = tok
    return headers


# --- configuration ---------------------------------------------------------

def test_off_by_default():
    assert load_config({}).mode == "off"


def test_incomplete_or_wrong_settings_switch_the_check_off():
    c = load_config({"SARCADE_ACCESS_VERIFY": "enforce", "SARCADE_ACCESS_TEAM_DOMAIN": TEAM})
    assert c.mode == "off" and "SARCADE_ACCESS_AUD" in c.error and "SARCADE_ACCESS_HOSTS" in c.error
    assert load_config({"SARCADE_ACCESS_VERIFY": "strict"}).error
    bad = load_config({"SARCADE_ACCESS_VERIFY": "log", "SARCADE_ACCESS_TEAM_DOMAIN": "evil.example.org",
                       "SARCADE_ACCESS_AUD": AUD, "SARCADE_ACCESS_HOSTS": HOST})
    assert bad.mode == "off" and bad.error


def test_full_settings():
    c = load_config({"SARCADE_ACCESS_VERIFY": "Enforce", "SARCADE_ACCESS_TEAM_DOMAIN": f"https://{TEAM}/",
                     "SARCADE_ACCESS_AUD": f"{AUD}, autre", "SARCADE_ACCESS_HOSTS": "App.Sarcade.org"})
    assert (c.mode, c.team_domain, c.audiences, c.hosts) == ("enforce", TEAM, (AUD, "autre"), (HOST,))
    assert c.applies_to("app.sarcade.org:443") and not c.applies_to("192.168.0.110:8000")


def test_host_normalisation():
    assert normal_host("APP.sarcade.org:443") == HOST
    assert normal_host("[::1]:8000") == "[::1]"


# --- token check -----------------------------------------------------------

def test_valid_token_gives_the_email():
    who = verifier().verify(token())
    assert who.kind == "user" and who.email == "operateur@example.org" and who.expires_at


@pytest.mark.parametrize("tok, reason", [
    (None, "missing_token"),
    ("pas-un-jeton", "malformed_token"),
    (token(exp=int(time.time()) - 600), "expired"),
    (token(aud=["autre-application"]), "wrong_audience"),
    (token(iss="https://autre.cloudflareaccess.com"), "wrong_issuer"),
    (token(key=OTHER), "bad_signature"),
    (token(kid="inconnue"), "unknown_key"),
    (token(exp=None), "invalid_token"),
])
def test_refusals(tok, reason):
    with pytest.raises(AccessDenied) as exc:
        verifier().verify(tok)
    assert exc.value.reason == reason


def test_hs256_token_is_refused():
    forged = jwt.encode({"aud": [AUD], "iss": f"https://{TEAM}", "exp": int(time.time()) + 60}, "secret-de-test-assez-long-pour-hs256",
                        algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(AccessDenied) as exc:
        verifier().verify(forged)
    assert exc.value.reason == "unexpected_algorithm"


def test_service_token_of_the_gateway():
    who = verifier().verify(token(email=None, common_name="abc.access", sub=""))
    assert who.kind == "service" and who.label == "jeton de service abc.access"


def test_key_rotation_fetches_again_once_a_minute():
    now = [1000.0]
    certs = FakeCerts(jwk(KEY, "k1"))
    v = verifier(certs=certs, clock=lambda: now[0])
    v.verify(token())
    assert certs.calls == 1
    certs.keys.append(jwk(OTHER, "k2"))
    with pytest.raises(AccessDenied):
        v.verify(token(key=OTHER, kid="k2"))  # just fetched: no new attempt
    now[0] += 61
    assert v.verify(token(key=OTHER, kid="k2")).email
    assert certs.calls == 2


def test_keys_unreachable_keeps_the_old_keys():
    now = [0.0]
    calls = {"n": 0}

    def certs(url):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("réseau coupé")
        return {"keys": [jwk(KEY, "k1")]}
    v = verifier(certs=certs, clock=lambda: now[0])
    v.verify(token())
    now[0] += 4000  # cache expired, refresh fails
    assert v.verify(token()).email
    assert "réseau coupé" in v.keys.last_error


def test_no_keys_at_all():
    def certs(url):
        raise OSError("réseau coupé")
    with pytest.raises(AccessDenied) as exc:
        verifier(certs=certs).verify(token())
    assert exc.value.reason == "keys_unavailable"


# --- middleware --------------------------------------------------------------

def test_enforce_refuses_public_requests_without_token(use):
    client = use(verifier())
    r = client.get("/api/v0.1/events", headers=public())
    assert r.status_code == 403
    assert r.json()["detail"] == "access_denied"
    assert "cloudflare-access" in r.headers["www-authenticate"].lower()
    assert r.headers["x-sarcade-access"] == "missing_token"


def test_enforce_lets_the_local_address_through(use):
    client = use(verifier())
    r = client.get("/api/v0.1/access/session", headers={"host": "192.168.0.110:8000"})
    assert r.status_code == 200 and r.json()["protected"] is False


def test_health_stays_open(use):
    client = use(verifier())
    assert client.get("/health", headers=public()).status_code == 200


def test_valid_token_reaches_the_endpoint_verified(use):
    client = use(verifier())
    r = client.get("/api/v0.1/access/session", headers=public(token()))
    assert r.status_code == 200
    body = r.json()
    assert body["protected"] is True and body["verified"] is True and body["email"] == "operateur@example.org"


def test_cookie_of_the_applications_is_accepted(use):
    client = use(verifier())
    client.cookies.set("CF_Authorization", token())
    r = client.get("/api/v0.1/access/session", headers=public())
    assert r.json()["verified"] is True


def test_log_mode_never_blocks_but_counts(use):
    v = verifier(mode="log")
    client = use(v)
    r = client.get("/api/v0.1/access/session", headers=public(token(aud=["autre"])))
    assert r.status_code == 200
    assert r.json()["verified"] is False and r.json()["verify_reason"] == "wrong_audience"
    status = client.get("/api/v0.1/access/status", headers=public(token())).json()
    assert status["mode"] == "log" and status["counts"]["denied:wrong_audience"] == 1
    assert status["counts"]["accepted"] == 1 and status["audiences"] == 1
    assert AUD not in str(status)


def test_websocket_refused_without_token(use):
    client = use(verifier())
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v0.1/events/x/ws", headers=public()):
            pass


def test_websocket_accepted_with_token(use):
    client = use(verifier())
    with client.websocket_connect("/api/v0.1/events/x/ws", headers=public(token())) as ws:
        ws.close()


def test_off_mode_changes_nothing(use):
    client = use(Verifier(AccessConfig()))
    assert client.get("/api/v0.1/access/status", headers=public()).status_code == 200
    r = client.get("/api/v0.1/access/session", headers=public(token()))
    assert r.status_code == 200 and r.json()["verified"] is None


# --- main courante ---------------------------------------------------------

def test_actor_of_operations():
    assert access_logbook.actor_of("position", {"device_id": "ADR78-01", "updated_by": "x"}) == "ADR78-01"
    assert access_logbook.actor_of("message", {"sender_id": "PCO"}) == "PCO"
    assert access_logbook.actor_of("map_feature", {"updated_by": "ADR78-02"}) == "ADR78-02"
    assert access_logbook.actor_of("map_feature", {}) is None


def test_logbook_summary():
    user = AccessIdentity(kind="user", email="operateur@example.org")
    assert access_logbook.summary("ADR78-01", user) == \
        "ADR78-01 connecté par Cloudflare Access : operateur@example.org"
    service = AccessIdentity(kind="service", common_name="gw.access")
    assert "jeton de service gw.access" in access_logbook.summary("GW-1", service)


@pytest.mark.skipif(not os.getenv("SARCADE_DB_TESTS"), reason="needs PostgreSQL (SARCADE_DB_TESTS=1)")
def test_sync_writes_the_email_once_in_the_logbook(use):
    client = use(verifier())
    event = client.post("/api/v0.1/events", json={"name": "Access", "kind": "exercise"},
                        headers=public(token())).json()["id"]

    def op():
        oid = str(uuid.uuid4())
        return {"operation_id": oid, "event_id": event, "object_id": oid, "object_type": "message",
                "action": "create", "client_time": "2026-10-24T10:00:00Z",
                "payload": {"id": oid, "event_id": event, "sender_id": "ADR78-01", "recipient_ids": [],
                            "body": "essai", "priority": "routine",
                            "created_at": "2026-10-24T10:00:00Z"}}
    for _ in range(2):
        r = client.post("/api/v0.1/sync", json=[op(), op()], headers=public(token()))
        assert [x["status"] for x in r.json()] == ["accepted", "accepted"]
    lines = client.get(f"/api/v0.1/events/{event}/logbook", headers=public(token())).json()
    access = [x for x in lines if x.get("kind") == "access"]
    assert len(access) == 1
    assert access[0]["summary"] == "ADR78-01 connecté par Cloudflare Access : operateur@example.org"
