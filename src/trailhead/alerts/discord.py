"""Send alerts to a Discord channel through a webhook (stdlib only; never blocks the UI).

Building the message is pure; `DiscordSender` posts it on a background thread and reports the outcome through
a callback. Discord takes at most 10 embeds per message, so alerts go out in batches of 10.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime

from .. import __version__
from ..ui.theme import tokens
from .engine import Alert

log = logging.getLogger(__name__)

WEBHOOK_RE = re.compile(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+/?$")
MAX_EMBEDS = 10
TIMEOUT_S = 10
USERNAME = "Trailhead"


def _hex(c: str) -> int:
    return int(c.lstrip("#"), 16)


def _colors() -> dict[str, int]:
    p = tokens.DARK  # the embed's left stripe, same colours as in the app
    return {"STOP_HIT": _hex(p.negative), "NEAR_STOP": _hex(p.stop), "TARGET_SELL": _hex(p.positive),
            "TARGET_STOP": _hex(p.target), "EARNINGS_SOON": _hex(p.info)}


def valid_webhook(url: str) -> bool:
    return bool(WEBHOOK_RE.match(url.strip()))


def selected(alerts: Iterable[Alert], categories: Iterable[str]) -> list[Alert]:
    cats = set(categories)
    return [a for a in alerts if a.category in cats]


def payloads(alerts: Sequence[Alert], portfolio_names: dict[int, str], now: datetime | None = None) -> list[dict]:
    """One webhook body per 10 alerts: an embed per alert, coloured by category, portfolio in the footer."""
    stamp = (now or datetime.now(UTC)).isoformat()
    colors = _colors()
    embeds = [{
        "title": a.title,
        "description": a.message,
        "color": colors.get(a.category, colors["TARGET_STOP"]),
        "footer": {"text": portfolio_names.get(a.portfolio_id, "")},
        "timestamp": stamp,
    } for a in alerts]
    return [{"username": USERNAME, "embeds": embeds[i:i + MAX_EMBEDS], "allowed_mentions": {"parse": []}}
            for i in range(0, len(embeds), MAX_EMBEDS)]


def test_payload() -> dict:
    return {"username": USERNAME, "allowed_mentions": {"parse": []},
            "content": "Trailhead is connected. Alerts you picked in Settings will arrive here."}


def post(url: str, body: dict) -> None:
    """POST one webhook body; raises on failure. Only ever posts to a Discord webhook URL."""
    if not valid_webhook(url):
        raise ValueError("Not a Discord webhook URL")
    req = urllib.request.Request(
        url.strip(), data=json.dumps(body).encode(), method="POST",
        # Discord rejects urllib's default agent
        headers={"Content-Type": "application/json", "User-Agent": f"Trailhead/{__version__}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        resp.read()


def describe_error(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 404):
            return "Discord doesn't know this webhook (deleted or mistyped?)"
        if e.code == 429:
            return "Discord is rate-limiting; try again shortly"
        return f"Discord answered HTTP {e.code}"
    if isinstance(e, urllib.error.URLError):
        return f"Couldn't reach Discord ({e.reason})"
    return str(e) or type(e).__name__


class DiscordSender:
    """Posts bodies in order on a daemon thread; `on_done(error or None)` runs on that thread."""

    def __init__(self, poster: Callable[[str, dict], None] = post):
        self.poster = poster
        self._lock = threading.Lock()

    def send(self, url: str, bodies: list[dict], on_done: Callable[[str | None], None] | None = None) -> None:
        def run() -> None:
            error = None
            with self._lock:
                for body in bodies:
                    try:
                        self.poster(url, body)
                    except Exception as e:  # network, HTTP, anything: report, never crash
                        log.info("Discord webhook failed", exc_info=True)
                        error = describe_error(e)
                        break
            if on_done is not None:
                try:
                    on_done(error)
                except RuntimeError:  # the receiver (e.g. a closed Settings dialog) is gone
                    log.debug("Discord result had no receiver", exc_info=True)

        threading.Thread(target=run, name="discord", daemon=True).start()
