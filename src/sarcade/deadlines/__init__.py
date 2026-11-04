# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""« Échéances » of the Diagnostic of the administrator (lot 28, card
« Diagnostic administrateur : Échéances », decided on 4 Nov 2026, 7 h 26;
takes over the card « Brevo : la clé SMTP expire »).

Everything that expires, its date, the days left and a colour: green;
orange at 30 days; red at 7 days; « expired » past the date.

- Detected by the server when possible: HTTPS certificate of the public
  address (``SARCADE_PUBLIC_URL``, read once a day), licence of the server
  and licences of the organisations, Brevo key unused for 90 days (last
  successful sending + 90 days).
- Typed by the administrator for the rest: name, date, link to the
  renewal page, note; never the secret itself.
- Alerts at 30 days, 7 days and on the day: email to the administrators
  (by Brevo; when Brevo itself is concerned, only in the application),
  red dot on Paramètres and banner in the Diagnostic. Check once a day.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import logging
import re
import socket
import ssl

from sqlalchemy.orm import Session

from sarcade.updates.service import get_value, set_value

log = logging.getLogger("sarcade.deadlines")

KEY = "deadlines"              # manual items
TLS_KEY = "deadline_tls"       # last reading of the HTTPS certificate
ALERTS_KEY = "deadline_alerts" # thresholds already announced, per item
ORANGE_DAYS, RED_DAYS = 30, 7
THRESHOLDS = (30, 7, 0)
MAIL_IDLE_DAYS = 90
MAX_ITEMS = 50
CHECK_INTERVAL_S = 24 * 3600
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

# Pre-filled list (card, point 3): the administrator completes the dates.
DEFAULTS = [
    {"id": "brevo-smtp", "name": "Clé SMTP Brevo", "date": "2027-11-03",
     "url": "https://app.brevo.com/settings/keys/smtp", "note": "Expire aussi après 90 jours sans envoi"},
    {"id": "domain", "name": "Domaine sarcade.org", "date": None, "url": None, "note": None},
    {"id": "apple-developer", "name": "Adhésion Apple Developer", "date": "2027-10-22",
     "url": "https://developer.apple.com/account", "note": None},
    {"id": "apple-asc-key", "name": "Clé Apple App Store Connect", "date": None, "url": None, "note": None},
    {"id": "windows-signing", "name": "Certificat de signature Windows", "date": None, "url": None, "note": None},
    {"id": "meteo-france", "name": "Clé Météo-France", "date": None, "url": None, "note": None},
    {"id": "opensky", "name": "Identifiants OpenSky", "date": None, "url": None, "note": None},
]


class InvalidDeadlines(ValueError):
    pass


def _day(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def level(days_left: int | None) -> str:
    """green, orange (30 days), red (7 days, and the day itself), expired, unknown."""
    if days_left is None:
        return "unknown"
    if days_left < 0:
        return "expired"
    if days_left <= RED_DAYS:
        return "red"
    return "orange" if days_left <= ORANGE_DAYS else "green"


@dataclass
class Item:
    id: str
    name: str
    date: date | None
    kind: str = "manual"           # manual or auto
    url: str | None = None
    note: str | None = None
    mail_related: bool = False     # Brevo concerned: alert only in the application

    def as_dict(self, today: date) -> dict:
        left = (self.date - today).days if self.date else None
        return {"id": self.id, "name": self.name, "date": self.date.isoformat() if self.date else None,
                "days_left": left, "level": level(left), "kind": self.kind, "url": self.url, "note": self.note,
                "mail_related": self.mail_related}


# --- Manual items -------------------------------------------------------------------

def manual(db: Session) -> list[dict]:
    v = get_value(db, KEY)
    if v is None:
        return [dict(d) for d in DEFAULTS]
    return [dict(d) for d in (v.get("items") if isinstance(v, dict) else v) or []]


def _clean_url(url) -> str | None:
    if url in (None, ""):
        return None
    url = str(url).strip()
    if len(url) > 300 or not url.startswith("https://") or " " in url:
        raise InvalidDeadlines("invalid_url")
    return url


def _slug(name: str) -> str:
    import unicodedata
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:40]


def validate(items) -> list[dict]:
    """Name, date (YYYY-MM-DD or null), https link, note; at most 50."""
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise InvalidDeadlines("too_many_items")
    out, seen = [], set()
    for raw in items:
        if not isinstance(raw, dict):
            raise InvalidDeadlines("invalid_item")
        name = " ".join(str(raw.get("name") or "").split())
        if not 1 <= len(name) <= 80:
            raise InvalidDeadlines("invalid_name")
        ident = str(raw.get("id") or "").strip().lower() or _slug(name)
        if not _ID.match(ident or "") or ident in seen:
            raise InvalidDeadlines("invalid_id")
        seen.add(ident)
        d = raw.get("date")
        if d not in (None, "") and _day(d) is None:
            raise InvalidDeadlines("invalid_date")
        note = " ".join(str(raw.get("note") or "").split())[:200] or None
        out.append({"id": ident, "name": name, "date": _day(d).isoformat() if _day(d) else None,
                    "url": _clean_url(raw.get("url")), "note": note})
    return out


def save(db: Session, items, by: str, now: datetime | None = None) -> list[dict]:
    clean = validate(items)
    set_value(db, KEY, {"items": clean, "by": by[:64]}, now)
    return clean


# --- Detected by the server -------------------------------------------------------------

def _licence_items(db: Session) -> list[Item]:
    out: list[Item] = []
    try:
        from sarcade import licensing
        s = licensing.state()
        lic = s.licence or {}
        if s.status != "none" and _day(lic.get("expires_at")):
            out.append(Item("licence-server", f"Licence SARCADE ({lic.get('organisation') or 'serveur'})",
                            _day(lic.get("expires_at")), "auto"))
    except Exception:  # noqa: BLE001 - unreadable licence: shown elsewhere
        log.debug("server licence not read", exc_info=True)
    try:
        from sarcade import roles
        if not roles.directory_ready(db):
            return out
        from sarcade.directory import graph as graphs, licences
        g = graphs.load(db)
        for gid, doc in sorted(licences.documents(db).items()):
            lic = (doc or {}).get("licence") or {}
            day = _day(lic.get("expires_at"))
            if day:
                modules = ", ".join(str(m).upper() if str(m) == "adrasec" else str(m) for m in lic.get("modules") or [])
                out.append(Item(f"licence-{gid}"[:40].lower(), f"Licence {modules or 'SARCADE'} · {g.name(gid)}", day, "auto"))
    except Exception:  # noqa: BLE001
        log.debug("licences of the organisations not read", exc_info=True)
    return out


def _mail_item(db: Session) -> Item | None:
    """The Brevo key expires after 90 days without sending."""
    from sarcade.accounts import mailer
    if mailer.config() is None:
        return None
    last = (mailer.stats(db) or {}).get("last_success_at")
    try:
        at = datetime.fromisoformat(str(last).replace("Z", "+00:00")) if last else None
    except ValueError:
        at = None
    if at is None:
        return None
    return Item("mail-inactivity", "Clé SMTP Brevo : 90 jours sans envoi", _day(at + timedelta(days=MAIL_IDLE_DAYS)),
                "auto", url="https://app.brevo.com/settings/keys/smtp",
                note="Un e-mail d'essai depuis le Diagnostic remet le compteur à zéro", mail_related=True)


def _tls_item(db: Session) -> Item | None:
    v = get_value(db, TLS_KEY) or {}
    if not isinstance(v, dict) or not v.get("not_after"):
        return None
    return Item("tls", f"Certificat HTTPS ({v.get('host')})", _day(v["not_after"]), "auto")


def tls_expiry(host: str, port: int = 443, timeout: float = 8) -> datetime:
    """End of validity of the certificate presented by ``host``, read
    without checking it (local certification authority, option B)."""
    from cryptography import x509
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as s:
            der = s.getpeercert(binary_form=True)
    return x509.load_der_x509_certificate(der).not_valid_after_utc


def read_tls(db: Session, now: datetime, reader=tls_expiry) -> None:
    from urllib.parse import urlparse
    from sarcade import tls
    url = tls.public_url()
    host = urlparse(url).hostname if url else None
    if not host:
        return
    try:
        end = reader(host)
        set_value(db, TLS_KEY, {"host": host, "not_after": end.isoformat(), "checked_at": now.isoformat()}, now)
    except Exception as exc:  # noqa: BLE001 - the last reading is kept
        prev = get_value(db, TLS_KEY) or {}
        set_value(db, TLS_KEY, {**(prev if isinstance(prev, dict) else {}), "host": host,
                                "error": type(exc).__name__, "checked_at": now.isoformat()}, now)


# --- Everything -------------------------------------------------------------------------

def items(db: Session) -> list[Item]:
    out = [Item(d["id"], d["name"], _day(d.get("date")), "manual", d.get("url"), d.get("note"),
                mail_related=str(d["id"]).startswith("brevo")) for d in manual(db)]
    out += [i for i in (_tls_item(db), _mail_item(db)) if i is not None]
    out += _licence_items(db)
    return out


ORDER = {"expired": 0, "red": 1, "orange": 2, "green": 3, "unknown": 4}


def view(db: Session, now: datetime | None = None) -> dict:
    """``items`` sorted by urgency and ``summary`` {worst, alerts}."""
    today = (now or datetime.now(UTC)).date()
    rows = sorted((i.as_dict(today) for i in items(db)),
                  key=lambda r: (ORDER[r["level"]], r["days_left"] if r["days_left"] is not None else 10**6, r["name"]))
    return {"items": rows, "summary": summary(rows), "tls": get_value(db, TLS_KEY)}


def summary(rows: list[dict]) -> dict:
    alerts = [r for r in rows if r["level"] in ("orange", "red", "expired")]
    worst = min((r["level"] for r in alerts), key=ORDER.get, default="green")
    return {"worst": worst, "alerts": len(alerts)}


# --- Alerts at 30 days, 7 days and on the day -----------------------------------------

def due_threshold(days_left: int | None, sent: list[int]) -> int | None:
    """The smallest threshold reached and not yet announced."""
    if days_left is None:
        return None
    reached = [t for t in THRESHOLDS if days_left <= t]
    if not reached or min(reached) in sent:
        return None
    return min(reached)


def admin_emails(db: Session) -> list[str]:
    from sarcade import roles
    out = set(roles.stored(db)["admins"]) | set(roles.env_admins())
    try:
        if roles.directory_ready(db):
            from sqlalchemy import select
            from sarcade.db.models import UserRow
            from sarcade.directory import rights
            ids = rights.active_admins(db)
            if ids:
                out |= set(db.scalars(select(UserRow.email).where(UserRow.id.in_(list(ids)))))
    except Exception:  # noqa: BLE001
        log.debug("administrators of the directory not read", exc_info=True)
    return sorted(e for e in out if e)


def alert_message(cfg, to: str, item: dict):
    from email.message import EmailMessage
    from email.utils import formataddr, make_msgid, parseaddr
    left = item["days_left"]
    when = ("expirée depuis le " if left < 0 else "expire aujourd'hui" if left == 0 else
            f"expire dans {left} jour{'s' if left > 1 else ''}, le ")
    when = when if left == 0 else when + item["date"]
    msg = EmailMessage()
    name, address = parseaddr(cfg.sender)
    msg["From"] = formataddr((name or "SARCADE", address or cfg.sender))
    msg["To"] = to
    msg["Subject"] = f"SARCADE : échéance « {item['name']} »"
    msg["Message-ID"] = make_msgid(domain=(address or "sarcade.org").rpartition("@")[2] or None)
    msg["Auto-Submitted"] = "auto-generated"
    lines = ["Bonjour,", "", f"« {item['name']} » {when}."]
    if item.get("url"):
        lines.append(f"Renouvellement : {item['url']}")
    lines += ["", "Détail : Paramètres › Diagnostic › Échéances.", "", "SARCADE", ""]
    msg.set_content("\n".join(lines))
    return msg


def check(db: Session, now: datetime | None = None, sender=None, tls_reader=tls_expiry) -> list[dict]:
    """Daily check: reads the certificate, announces the thresholds reached
    (email to the administrators unless Brevo is concerned), journals them.
    Returns the alerts given. The caller commits."""
    from sarcade.accounts import mailer
    from sarcade.security import journal
    now = now or datetime.now(UTC)
    read_tls(db, now, tls_reader)
    db.flush()
    state = get_value(db, ALERTS_KEY) or {}
    state = dict(state) if isinstance(state, dict) else {}
    cfg = mailer.config()
    send = sender or mailer.send
    given = []
    for row in view(db, now)["items"]:
        mine = state.get(row["id"]) or {}
        sent = list(mine.get("sent") or []) if mine.get("date") == row["date"] else []
        t = due_threshold(row["days_left"], sent)
        if t is None:
            continue
        emailed = 0
        if cfg is not None and not row["mail_related"]:
            for to in admin_emails(db):
                try:
                    send(cfg, alert_message(cfg, to, row))
                    emailed += 1
                except mailer.MailError as exc:
                    mailer.record(db, False, now, str(exc))
            if emailed:
                mailer.record(db, True, now)
        state[row["id"]] = {"date": row["date"], "sent": sorted({*sent, *[x for x in THRESHOLDS if x >= t]}),
                            "at": now.isoformat()}
        journal.record(db, "admin", "deadline_alert", "info", actor="SARCADE",
                       details={"id": row["id"], "name": row["name"], "days_left": row["days_left"],
                                "threshold": t, "emailed": emailed})
        given.append({**row, "threshold": t, "emailed": emailed})
    set_value(db, ALERTS_KEY, state, now)
    return given


def _check_now() -> None:
    from sarcade.db.session import SessionLocal
    db = SessionLocal()
    try:
        check(db)
        db.commit()
    finally:
        db.close()


async def check_loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await asyncio.to_thread(_check_now)
        except Exception as exc:  # noqa: BLE001
            log.warning("deadline check failed: %s", exc)
        await asyncio.sleep(CHECK_INTERVAL_S)


def start(tasks: list) -> None:
    import os
    if os.getenv("SARCADE_DEADLINE_CHECK", "1") != "0":
        tasks.append(asyncio.create_task(check_loop()))
