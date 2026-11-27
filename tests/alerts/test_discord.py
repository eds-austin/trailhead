import threading
import urllib.error
from datetime import UTC, datetime
from decimal import Decimal as D

import pytest

from trailhead.alerts import discord
from trailhead.alerts.engine import Alert, AlertKind


def _alert(kind=AlertKind.STOP_HIT, symbol="NVDA", sells=False, pid=1):
    return Alert(kind, pid, symbol, 1, D(114), f"{symbol} — x", "msg", sells)


def test_valid_webhook():
    ok = "https://discord.com/api/webhooks/123456/abc-DEF_9"
    assert discord.valid_webhook(ok) and discord.valid_webhook(f"  {ok}/ ")
    assert discord.valid_webhook("https://canary.discordapp.com/api/webhooks/1/x")
    for bad in ("", "http://discord.com/api/webhooks/1/x", "https://evil.com/api/webhooks/1/x",
                "https://discord.com.evil.com/api/webhooks/1/x", "https://discord.com/api/webhooks/abc/x"):
        assert not discord.valid_webhook(bad), bad


def test_selected_by_category():
    alerts = [_alert(), _alert(AlertKind.TARGET_REACHED, "A", sells=True), _alert(AlertKind.TARGET_REACHED, "B"),
              _alert(AlertKind.NEAR_STOP, "C")]
    assert [a.symbol for a in discord.selected(alerts, ["STOP_HIT", "TARGET_SELL"])] == ["NVDA", "A"]
    assert discord.selected(alerts, []) == []


def test_payloads_batch_ten_embeds_and_never_ping():
    alerts = [_alert(symbol=f"S{i}", pid=1 + i % 2) for i in range(12)]
    bodies = discord.payloads(alerts, {1: "Swing", 2: "Core"}, datetime(2026, 11, 26, tzinfo=UTC))
    assert [len(b["embeds"]) for b in bodies] == [10, 2]
    e = bodies[0]["embeds"][0]
    assert (e["title"], e["description"], e["footer"]["text"]) == ("S0 — x", "msg", "Swing")
    assert e["color"] == 0xF2545B and e["timestamp"].startswith("2026-11-26")
    assert all(b["allowed_mentions"] == {"parse": []} for b in bodies)


def test_post_refuses_other_urls():
    with pytest.raises(ValueError):
        discord.post("https://example.com/hook", {})


def test_describe_error():
    http = urllib.error.HTTPError("u", 404, "nf", {}, None)
    assert "webhook" in discord.describe_error(http)
    assert "rate" in discord.describe_error(urllib.error.HTTPError("u", 429, "", {}, None))
    assert "reach" in discord.describe_error(urllib.error.URLError("offline"))


def test_sender_posts_in_order_and_stops_on_error():
    sent, done = [], threading.Event()
    result = {}

    def poster(url, body):
        if body == "boom":
            raise urllib.error.HTTPError(url, 500, "", {}, None)
        sent.append(body)

    def on_done(err):
        result["err"] = err
        done.set()

    discord.DiscordSender(poster).send("u", ["a", "b", "boom", "c"], on_done)
    assert done.wait(5)
    assert sent == ["a", "b"] and result["err"] == "Discord answered HTTP 500"
