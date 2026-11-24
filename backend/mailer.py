"""Outgoing email through Resend (https://resend.com), using its HTTP API.

Configuration comes from the environment and is never printed:
  RESEND_API_KEY  sending key (Render's settings only; never in the repo)
  MAIL_FROM       sender, e.g. "Qiltsim <hello@qiltsim.com>"

Without RESEND_API_KEY nothing is sent: the message is written to the log instead,
which is how password reset links show up when running locally. The app keeps
email-dependent features switched off on a hosted site that has no key.

No Flask imports, on purpose (easy to test and to reuse from scripts).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Callable, Optional

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "Qiltsim <hello@qiltsim.com>"
TIMEOUT_SECONDS = 10

log = logging.getLogger("mailer")


class MailError(RuntimeError):
    """Sending failed. The message never includes the API key."""


def configured() -> bool:
    return bool((os.getenv("RESEND_API_KEY") or "").strip())


def sender() -> str:
    return (os.getenv("MAIL_FROM") or "").strip() or DEFAULT_FROM


def _post(request: urllib.request.Request) -> dict:
    with urllib.request.urlopen(
        request, timeout=TIMEOUT_SECONDS
    ) as resp:  # noqa: S310 (fixed https URL)
        return json.load(resp)


def send(
    to: str,
    subject: str,
    text: str,
    html: Optional[str] = None,
    *,
    reply_to: Optional[str] = None,
    post: Callable[[urllib.request.Request], dict] = _post,
) -> str:
    """Send one email. Returns Resend's message id, or "logged" when no key is set.

    reply_to: where a reply goes (e.g. the person who sent feedback)."""
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    if not api_key:
        log.warning(
            "Email not sent (no RESEND_API_KEY).\nTo: %s\nSubject: %s\n\n%s", to, subject, text
        )
        return "logged"
    payload = {"from": sender(), "to": [to], "subject": subject, "text": text}
    if html:
        payload["html"] = html
    if reply_to:
        payload["reply_to"] = [reply_to]
    request = urllib.request.Request(
        RESEND_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "qiltsim-app",
        },
        method="POST",
    )
    try:
        data = post(request)
    except urllib.error.HTTPError as e:
        raise MailError(f"Resend refused the email (HTTP {e.code})") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise MailError(f"Could not reach Resend ({type(e).__name__})") from None
    return str((data or {}).get("id") or "sent")
