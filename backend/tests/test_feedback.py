"""
Форма обратной связи → Telegram: лимит частоты и длины (перенесено из MINMAXapp
2026-10-22 — публичная копия на Render без лимита давала спамить в чат владельца).

Сеть не используется: подменяется urllib.request.urlopen, и тест видит ровно
то тело запроса, которое ушло бы в Telegram.
"""
from __future__ import annotations

import json

import pytest

from backend.app.routers import feedback


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def sent(monkeypatch):
    """Список тел запросов, «отправленных» в Telegram."""
    bodies: list[dict] = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(
        feedback.urllib.request, "urlopen",
        lambda req, timeout: bodies.append(json.loads(req.data)) or _FakeResponse(),
    )
    feedback._recent.clear()
    yield bodies
    feedback._recent.clear()


def test_message_with_markdown_characters_is_sent_as_plain_text(client, sent):
    """С parse_mode=Markdown непарные «_» и «*» давали отказ Telegram → 502."""
    msg = "snake_case и 2*3, ссылка [x](y"

    r = client.post("/feedback", json={"message": msg, "page": "calc_page"})

    assert r.status_code == 200
    assert "parse_mode" not in sent[0]
    assert msg in sent[0]["text"]


def test_too_long_message_is_rejected(client, sent):
    r = client.post("/feedback", json={"message": "x" * (feedback.MAX_MESSAGE_LEN + 1)})

    assert r.status_code == 422
    assert sent == []


def test_rate_limit_per_client(client, sent):
    for _ in range(feedback.RATE_LIMIT):
        assert client.post("/feedback", json={"message": "hi"}).status_code == 200

    r = client.post("/feedback", json={"message": "hi"})

    assert r.status_code == 429
    assert r.json()["detail"] == "feedback_rate_limited"
    assert int(r.headers["retry-after"]) == feedback.RATE_WINDOW_S
    assert len(sent) == feedback.RATE_LIMIT


def test_empty_message_does_not_spend_the_limit(client, sent):
    for _ in range(feedback.RATE_LIMIT + 2):
        assert client.post("/feedback", json={"message": "   "}).status_code == 400
    assert client.post("/feedback", json={"message": "hi"}).status_code == 200


# ─── Адрес клиента за прокси ─────────────────────────────────────────────────
# Прокси дописывает адрес в конец X-Forwarded-For; всё левее прислал клиент сам.

def _post(client, xff):
    return client.post("/feedback", json={"message": "hi"}, headers={"X-Forwarded-For": xff})


def test_spoofed_left_entries_do_not_reset_the_limit(client, sent, monkeypatch):
    """Раньше ключом была первая запись — новый выдуманный адрес в каждом запросе снимал лимит."""
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    codes = [_post(client, f"6.6.6.{i}, 203.0.113.5").status_code for i in range(feedback.RATE_LIMIT + 1)]
    assert codes[-1] == 429 and len(sent) == feedback.RATE_LIMIT
    # Другой настоящий адрес (его дописал прокси) — свой лимит
    assert _post(client, "6.6.6.99, 198.51.100.7").status_code == 200


def test_header_is_ignored_without_trusted_proxy(client, sent, monkeypatch):
    """Без прокси (TRUSTED_PROXY_HOPS=0) заголовок целиком пишет клиент — ему не верим."""
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    codes = [_post(client, f"10.0.0.{i}").status_code for i in range(feedback.RATE_LIMIT + 1)]
    assert codes[-1] == 429


def test_short_chain_falls_back_to_the_peer(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    req = type("R", (), {"headers": {"x-forwarded-for": "203.0.113.5"},
                         "client": type("C", (), {"host": "10.9.9.9"})()})()
    assert feedback._client_key(req) == "10.9.9.9"
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    req.headers = {"x-forwarded-for": "6.6.6.6, 203.0.113.5, 10.0.0.1"}
    assert feedback._client_key(req) == "203.0.113.5"


def test_tracked_clients_are_bounded(monkeypatch):
    """Словарь адресов не растёт без предела: истёкшие окна выбрасываются при переполнении."""
    monkeypatch.setattr(feedback, "MAX_TRACKED_CLIENTS", 3)
    feedback._recent.clear()
    for k in "abc":
        assert feedback._allow(k, 0.0)
    assert not feedback._allow("d", 1.0)                       # все окна живые
    assert feedback._allow("d", feedback.RATE_WINDOW_S + 1.0)  # истёкшие выброшены
    assert set(feedback._recent) == {"d"}
    feedback._recent.clear()


def test_rate_window_expires():
    feedback._recent.clear()
    for _ in range(feedback.RATE_LIMIT):
        assert feedback._allow("k", 0.0)
    assert not feedback._allow("k", 1.0)
    assert feedback._allow("k", feedback.RATE_WINDOW_S + 1.0)
    feedback._recent.clear()
