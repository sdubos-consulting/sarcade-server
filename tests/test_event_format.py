# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Common event format, ECS names (lot 29, group 8)."""
from datetime import UTC, datetime
import json
from types import SimpleNamespace

from sarcade.security import event_format, siem


def _sec(**kw):
    base = dict(seq=4410, at=datetime(2026, 11, 5, 21, 20, 11, 512000, tzinfo=UTC), category="auth", action="login",
                outcome="refused", actor="x@example.org", device_id=None, event_id=None, source_ip="203.0.113.8",
                details={}, hash="ab" * 32, prev_hash="0" * 64)
    base.update(kw)
    return SimpleNamespace(**base)


def test_security_entry():
    d = event_format.from_security(_sec())
    assert d["@timestamp"] == "2026-11-05T21:20:11.512Z"
    assert d["event"]["id"] == "sec-4410" and d["event"]["action"] == "auth.login"
    assert d["event"]["outcome"] == "failure" and d["event"]["category"] == ["authentication"]
    assert d["user"]["id"] == "x@example.org" and d["source"]["ip"] == "203.0.113.8"
    assert "host" not in d, "empty values are dropped"


def test_logbook_entry_with_position():
    row = SimpleNamespace(seq=812, time=datetime(2026, 11, 5, 21, 17, 4, tzinfo=UTC), kind="message", actor_id="TEL-01",
                          summary="🆘 SOS de TEL-01 (48.80000, 2.13000)", event_id="e1", object_id="m1", hash="cd" * 32)
    d = event_format.from_logbook(row)
    assert d["event"]["id"] == "log-812" and d["event"]["dataset"] == "sarcade.logbook"
    assert d["geo"]["location"] == {"lat": 48.8, "lon": 2.13}
    flat = event_format.flat(d)
    assert [c for c in event_format.CSV_COLUMNS if c in flat][:4] == ["event.id", "@timestamp", "event.dataset", "event.action"]


def test_siem_sends_the_common_format():
    line = siem.render(_sec(), "ecs")
    assert json.loads(line)["event"]["id"] == "sec-4410"
    assert siem.render(_sec(), "syslog").startswith("<")
