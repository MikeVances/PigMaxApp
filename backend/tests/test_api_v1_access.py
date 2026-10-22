"""
Ключи X-API-Key и лимит частоты API v1 — перенесено из MINMAXapp
(backend/tests/test_api_v1_access.py, 59390c7) вместе с api_access.py.

Зачем: публичная копия без ключа отдавала бы /climate-designs кому угодно — перебор
координат тратит квоту Open-Meteo. Тесты держат четыре обещания: без API_KEYS
доступ открыт; с ключами чужой запрос получает 401 раньше любой работы; у каждого
ключа свои лимиты по группам; документация открыта всегда.

Расчётная группа (сценарии) и сверка ответов с контрактом — в тестах этапа 6б.
"""
from __future__ import annotations

import json

import pytest

from backend.app import api_access, api_v1
from backend.app.api_access import RateLimiter

# Путь журнала — у самого приложения: импорт conftest выполнил бы его второй раз
# (второй временный каталог), и тест читал бы не тот файл
AUDIT_LOG = api_v1.AUDIT_LOG_PATH

KEY_A = "agent-alpha-7f3c9e"
KEY_B = "agent-beta-1d8a42"
CLIMATE = {"latitude": 45.0, "longitude": 39.0}


def _entry(request_id: str) -> dict:
    for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("request_id") == request_id:
            return e
    raise AssertionError(f"{request_id} not in audit log")


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("API_KEYS", f" {KEY_A} , {KEY_B} ,")   # пробелы и пустой хвост — как набирают руками
    api_access.limiter.reset()
    yield
    api_access.limiter.reset()


@pytest.fixture
def tight(monkeypatch, keys):
    # Малые лимиты: доходим до 429 за несколько запросов, а не за 300
    small = RateLimiter({"calc": 2, "catalog": 3, "climate": 1})
    monkeypatch.setattr(api_access, "limiter", small)
    return small


@pytest.fixture
def fake_climate(monkeypatch):
    calls = []
    n = 24 * 366

    def fake_fetch(*args, **_k):
        calls.append(args)
        return [-20 + 60 * i / (n - 1) for i in range(n)], [50.0] * n, 150.0
    monkeypatch.setattr(api_v1.design_climate, "fetch_hourly", fake_fetch)
    api_v1._climate_cache.clear()
    yield calls
    api_v1._climate_cache.clear()


def _problem(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == code
    assert body["instance"] == r.headers["x-request-id"]
    return body


# ─── Открытый режим ───────────────────────────────────────────────────────────

def test_without_api_keys_access_stays_open(client, monkeypatch):
    # Регрессионный сторож: LAN и локальная работа без ключей — открыто
    monkeypatch.delenv("API_KEYS", raising=False)
    assert client.get("/api/v1/sources").status_code == 200


# ─── Ключ ─────────────────────────────────────────────────────────────────────

def test_missing_key_is_401_problem_in_the_log(client, keys):
    body = _problem(client.get("/api/v1/sources"), 401, "api_key_required")
    entry = _entry(body["instance"])
    assert entry["status"] == 401 and "client" not in entry


@pytest.mark.parametrize("header", ["wrong-key", KEY_A + " ", KEY_A.upper()])
def test_wrong_key_is_401(client, keys, header):
    _problem(client.get("/api/v1/genetics", headers={"X-API-Key": header}), 401, "api_key_required")


def test_non_ascii_key_is_401_not_500(client, keys):
    # compare_digest на str с не-ASCII бросает TypeError — сравниваем байты
    r = client.get("/api/v1/sources", headers={"X-API-Key": "ключ".encode("utf-8")})
    assert r.status_code == 401


def test_unknown_path_without_key_is_401_not_404(client, keys):
    # Перебор адресов без ключа не отличает «нет маршрута» от «нет ключа»
    assert client.get("/api/v1/admin").status_code == 401


@pytest.mark.parametrize("key", [KEY_A, KEY_B])
def test_each_key_works_and_is_logged_by_fingerprint(client, keys, fake_climate, key):
    r = client.get("/api/v1/climate-designs", params=CLIMATE, headers={"X-API-Key": key})
    assert r.status_code == 200
    entry = _entry(r.json()["request_id"])
    assert entry["client"] == api_access.key_id(key)
    # Сам ключ в журнал не попадает — журнал читают не только владельцы ключей
    assert key not in AUDIT_LOG.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", ["/openapi.yaml", "/openapi.json", "/docs"])
def test_documentation_is_open_without_key(client, keys, path):
    # Платформа агента импортирует контракт ДО того, как ей вписали ключ.
    # Контракт ещё не выложен (этап 6б) — 404, но не 401
    assert client.get(f"/api/v1{path}").status_code in (200, 404)
    assert client.get(f"/api/v1{path}").status_code != 401


def test_keys_do_not_touch_the_ui(client, keys):
    # Интерфейс ходит в свои маршруты, не в /api/v1 — ключ ему не нужен
    assert client.get("/version").status_code == 200
    assert client.post("/calc/ventilation", json={"weight_kg": 50, "num_pigs": 10, "t_outside_c": 0}).status_code == 200


# ─── Лимит частоты ────────────────────────────────────────────────────────────

def test_catalog_limit_gives_429_with_retry_after(client, tight):
    h = {"X-API-Key": KEY_A}
    for _ in range(3):
        assert client.get("/api/v1/sources", headers=h).status_code == 200
    r = client.get("/api/v1/sources", headers=h)
    body = _problem(r, 429, "rate_limited")
    assert body["params"] == {"limit": 3, "window_s": 60}
    assert 1 <= int(r.headers["retry-after"]) <= 60
    assert _entry(body["instance"])["client"] == api_access.key_id(KEY_A)


def test_limits_are_per_key_and_per_group(client, tight, fake_climate):
    a, b = {"X-API-Key": KEY_A}, {"X-API-Key": KEY_B}
    for _ in range(3):
        client.get("/api/v1/sources", headers=a)
    assert client.get("/api/v1/sources", headers=a).status_code == 429
    # Справочники ключа A исчерпаны — климат A и справочники B живут отдельно
    assert client.get("/api/v1/climate-designs", params=CLIMATE, headers=a).status_code == 200
    assert client.get("/api/v1/sources", headers=b).status_code == 200


def test_climate_limit_stops_before_open_meteo(client, tight, fake_climate):
    # Ради этого лимит и делался: отказ раньше внешнего запроса, квота не тратится
    h = {"X-API-Key": KEY_A}
    assert client.get("/api/v1/climate-designs", params=CLIMATE, headers=h).status_code == 200
    r = client.get("/api/v1/climate-designs", params={"latitude": 55.0, "longitude": 37.0}, headers=h)
    _problem(r, 429, "rate_limited")
    assert len(fake_climate) == 1


def test_documentation_is_not_counted(client, tight):
    for _ in range(5):
        assert client.get("/api/v1/openapi.yaml", headers={"X-API-Key": KEY_A}).status_code != 429
    assert client.get("/api/v1/sources", headers={"X-API-Key": KEY_A}).status_code == 200


def test_production_limits_match_minmax():
    assert api_access.LIMITS == {"calc": 60, "catalog": 300, "climate": 10}


# ─── Окно ─────────────────────────────────────────────────────────────────────

def test_sliding_window_and_retry_after():
    lim = RateLimiter({"calc": 2}, window_s=60)
    assert lim.hit("k", "calc", now=0).allowed
    assert lim.hit("k", "calc", now=10).allowed
    # Полное окно: место освободится, когда выйдет отметка t=0 — через 30 с
    v = lim.hit("k", "calc", now=30)
    assert not v.allowed and v.retry_after_s == 30
    assert lim.hit("k", "calc", now=60).allowed          # t=0 вышла из окна
    assert not lim.hit("k", "calc", now=60.5).allowed    # в окне t=10 и t=60
    # Отказ не занимает место: иначе клиент, повторяющий запрос, не дождался бы никогда
    assert lim.hit("k", "calc", now=70).allowed


def test_retry_after_is_never_zero():
    lim = RateLimiter({"calc": 1}, window_s=60)
    lim.hit("k", "calc", now=0)
    assert lim.hit("k", "calc", now=59.99).retry_after_s == 1


def test_zero_limit_closes_the_group():
    v = RateLimiter({"climate": 0}, window_s=60).hit("k", "climate", now=0)
    assert not v.allowed and v.retry_after_s == 60


# ─── Справочники и ошибки ─────────────────────────────────────────────────────

def test_climate_design_by_hand(client, fake_climate):
    # Равномерно −20 … +40 °C за год: 0.4 % часов ниже −19.76, выше +39.76 → округление 0.1
    j = client.get("/api/v1/climate-designs", params=CLIMATE).json()
    assert j["winter_design_c"] == pytest.approx(-19.8, abs=0.05)
    assert j["summer_design_db_c"] == pytest.approx(39.8, abs=0.05)
    assert j["source_id"] == "era5_open_meteo" and j["request_id"].startswith("req-")


def test_climate_is_cached_per_tenth_of_degree(client, fake_climate):
    client.get("/api/v1/climate-designs", params=CLIMATE)
    client.get("/api/v1/climate-designs", params={"latitude": 45.04, "longitude": 39.03})
    assert len(fake_climate) == 1


def test_unknown_genetics_is_404_problem(client):
    body = _problem(client.get("/api/v1/genetics/nope"), 404, "genetics_not_found")
    assert body["params"] == {"id": "nope"}


def test_genetics_profile_exposes_curves(client):
    j = client.get("/api/v1/genetics/klimaatplatform_2021").json()
    assert j["setpoint_meaning"] == "ventilation_start"
    assert j["finisher"]["max_vent_by_weight"][-1] == [120.06, 80]


def test_bad_source_id_is_validation_problem(client):
    body = _problem(client.get("/api/v1/sources/Bad-ID"), 422, "validation_failed")
    assert body["errors"][0]["field"] == "path.source_id"


def test_missing_contract_is_a_problem_not_a_crash(client, monkeypatch, tmp_path):
    monkeypatch.setattr(api_v1, "CONTRACT_PATH", tmp_path / "nope.yaml")
    _problem(client.get("/api/v1/openapi.yaml"), 404, "contract_not_found")
