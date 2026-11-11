from sarcade import tls


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("SARCADE_TLS_MODE", raising=False)
    monkeypatch.setenv("SARCADE_PUBLIC_URL", "https://adrasec78.sarcade.org")
    assert tls.state() == {"mode": "off", "https_url": None, "root_certificate_url": None}


def test_option_a(monkeypatch):
    monkeypatch.setenv("SARCADE_TLS_MODE", "acme-dns")
    monkeypatch.setenv("SARCADE_PUBLIC_URL", "https://adrasec78.sarcade.org/")
    s = tls.state()
    assert s["https_url"] == "https://adrasec78.sarcade.org"
    assert s["root_certificate_url"] is None


def test_option_b_root_certificate(monkeypatch):
    monkeypatch.setenv("SARCADE_TLS_MODE", "local-ca")
    monkeypatch.setenv("SARCADE_PUBLIC_URL", "https://sarcade.local")
    assert tls.state()["root_certificate_url"] == "http://sarcade.local/sarcade-root.crt"


def test_plain_http_url_is_ignored(monkeypatch):
    monkeypatch.setenv("SARCADE_TLS_MODE", "local-ca")
    monkeypatch.setenv("SARCADE_PUBLIC_URL", "http://sarcade.local")
    assert tls.state()["https_url"] is None
    monkeypatch.setenv("SARCADE_TLS_MODE", "bogus")
    assert tls.mode() == "off"
