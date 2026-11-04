# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Sending of the connection codes by email (decision of 3 Nov 2026:
Brevo, sender ``no-reply@sarcade.org`` once the domain is checked).

Settings, read from the environment of the server (``.env`` of the NAS,
placed by the project holder, never in the repository):

``SARCADE_SMTP_HOST``      e.g. ``smtp-relay.brevo.com``
``SARCADE_SMTP_PORT``      ``587`` (STARTTLS, default) or ``465`` (TLS)
``SARCADE_SMTP_USER``      SMTP login given by Brevo
``SARCADE_SMTP_PASSWORD``  SMTP key given by Brevo
``SARCADE_SMTP_FROM``      ``SARCADE <no-reply@sarcade.org>``

While one of them is missing, the sending is *simulated*: the code is
written in the security journal, readable by the administrator in the
console, and never in the ordinary application logs.
"""
from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
import logging
import os
import smtplib
import ssl

log = logging.getLogger("sarcade.accounts.mail")
ENV = ("SARCADE_SMTP_HOST", "SARCADE_SMTP_PORT", "SARCADE_SMTP_USER", "SARCADE_SMTP_PASSWORD", "SARCADE_SMTP_FROM")
TIMEOUT_S = 15


class MailError(Exception):
    pass


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    sender: str

    @property
    def sender_address(self) -> str:
        return parseaddr(self.sender)[1] or self.sender


def config(env: dict | None = None) -> SmtpConfig | None:
    """The SMTP settings, or None when the sending is simulated."""
    env = os.environ if env is None else env
    host = (env.get("SARCADE_SMTP_HOST") or "").strip()
    user = (env.get("SARCADE_SMTP_USER") or "").strip()
    password = env.get("SARCADE_SMTP_PASSWORD") or ""
    sender = (env.get("SARCADE_SMTP_FROM") or "").strip()
    try:
        port = int((env.get("SARCADE_SMTP_PORT") or "587").strip())
    except ValueError:
        port = 587
    if not (host and user and password and sender):
        return None
    return SmtpConfig(host=host, port=port, user=user, password=password, sender=sender)


def delivery(env: dict | None = None) -> str:
    return "smtp" if config(env) is not None else "simulated"


def state() -> dict:
    """For the console: never the login nor the key."""
    c = config()
    return {"delivery": "smtp" if c else "simulated", "host": c.host if c else None,
            "port": c.port if c else None, "sender": c.sender_address if c else None,
            "missing": [k for k in ENV if not (os.getenv(k) or "").strip() and k != "SARCADE_SMTP_PORT"]}


def code_message(cfg: SmtpConfig, to: str, code: str, minutes: int) -> EmailMessage:
    msg = EmailMessage()
    name, address = parseaddr(cfg.sender)
    msg["From"] = formataddr((name or "SARCADE", address or cfg.sender))
    msg["To"] = to
    msg["Subject"] = f"SARCADE : votre code de connexion {code}"
    msg["Message-ID"] = make_msgid(domain=(address or "sarcade.org").rpartition("@")[2] or None)
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(
        "Bonjour,\n\n"
        f"Votre code de connexion à SARCADE : {code}\n\n"
        f"Il est valable {minutes} minutes et ne sert qu'une fois.\n"
        "Si vous n'avez rien demandé, ignorez ce message.\n\n"
        "SARCADE\n")
    return msg


def reason(exc: BaseException) -> str:
    """« SMTPAuthenticationError 535 Authentication failed »: the type and
    the answer of the SMTP server, never the login, the key nor the address."""
    text = type(exc).__name__
    code = getattr(exc, "smtp_code", None)
    answer = getattr(exc, "smtp_error", None)
    if isinstance(answer, bytes):
        answer = answer.decode("utf-8", "replace")
    if code:
        text += f" {code}"
    if answer:
        text += " " + " ".join(str(answer).split())[:160]
    return text


def _session(cfg: SmtpConfig, action, timeout: float = TIMEOUT_S) -> None:
    context = ssl.create_default_context()
    if cfg.port == 465:
        with smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=timeout, context=context) as smtp:
            smtp.login(cfg.user, cfg.password)
            action(smtp)
    else:
        with smtplib.SMTP(cfg.host, cfg.port, timeout=timeout) as smtp:
            smtp.starttls(context=context)
            smtp.login(cfg.user, cfg.password)
            action(smtp)


def send(cfg: SmtpConfig, msg: EmailMessage) -> None:
    """Blocking: call it from a thread. Raises MailError."""
    try:
        _session(cfg, lambda smtp: smtp.send_message(msg))
    except (smtplib.SMTPException, OSError) as exc:
        # The reason only (no address, no code) in the application log.
        log.warning("Envoi du code de connexion impossible : %s", type(exc).__name__)
        raise MailError(reason(exc)) from exc


def probe(cfg: SmtpConfig, timeout: float = 8) -> None:
    """Connection and identification to the SMTP server, without sending
    (Diagnostic of the administrator, lot 28). Raises MailError."""
    try:
        _session(cfg, lambda smtp: None, timeout)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(reason(exc)) from exc


def test_message(cfg: SmtpConfig, to: str, server: str) -> EmailMessage:
    """« Envoyer un e-mail d'essai » of the Diagnostic (lot 28)."""
    msg = EmailMessage()
    name, address = parseaddr(cfg.sender)
    msg["From"] = formataddr((name or "SARCADE", address or cfg.sender))
    msg["To"] = to
    msg["Subject"] = "SARCADE : e-mail d'essai"
    msg["Message-ID"] = make_msgid(domain=(address or "sarcade.org").rpartition("@")[2] or None)
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(
        "Bonjour,\n\n"
        f"Cet e-mail d'essai a été envoyé depuis le Diagnostic de SARCADE ({server}).\n"
        "L'envoi des codes de connexion fonctionne.\n\n"
        "SARCADE\n")
    return msg


# --- Last sendings (lot 28): kept in the server settings ------------------------------

STATS_KEY = "mail_stats"


def stats(db) -> dict:
    """``last_success_at``, ``last_failure_at``, ``last_failure`` (reason)."""
    from sarcade.updates.service import get_value
    try:
        v = get_value(db, STATS_KEY, {}) or {}
    except Exception:  # noqa: BLE001 - settings unavailable (unit tests)
        return {}
    return {k: v.get(k) for k in ("last_success_at", "last_failure_at", "last_failure")} if isinstance(v, dict) else {}


def record(db, ok: bool, now, failure: str | None = None) -> None:
    """Writes the last success or failure; the caller commits."""
    from sarcade.updates.service import set_value
    try:
        v = dict(stats(db))
        at = now.isoformat().replace("+00:00", "Z")
        if ok:
            v["last_success_at"] = at
        else:
            v.update(last_failure_at=at, last_failure=(failure or "")[:200])
        set_value(db, STATS_KEY, v, now)
    except Exception:  # noqa: BLE001 - never blocks a sending
        log.debug("mail stats not written", exc_info=True)
