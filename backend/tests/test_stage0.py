"""
Этап 0: изоляция данных, защита записи и мелкие ошибки, найденные при осмотре
2026-10-22 (docs/sources.md).
"""
from __future__ import annotations

import json
import logging

import pytest

from backend.app.core import pig_settings
from backend.app.routers import feedback

VENT = {"weight_kg": 34.0, "num_pigs": 1000, "t_outside_c": -10.0}
SETTINGS = {"building": {"length_m": 60.0, "width_m": 12.0, "height_m": 2.5}}


# ─── Настройки: изоляция и запись ─────────────────────────────────────────────

def test_settings_go_to_the_env_path(client, settings_file):
    # Регрессионный сторож изоляции: запись — во временный файл из окружения
    assert client.put("/settings", json=SETTINGS).status_code == 200
    assert json.loads(settings_file.read_text(encoding="utf-8"))["houses"]["1"]["building"]["length_m"] == 60.0
    assert client.get("/settings").json()["building"]["floor_area_m2"] == 720.0


def test_save_leaves_no_temp_file(client, settings_file):
    # Регрессионный сторож атомарной записи
    client.put("/settings", json=SETTINGS)
    assert not settings_file.with_suffix(".json.tmp").exists()


def test_corrupt_settings_are_logged_not_silently_replaced(client, settings_file, caplog):
    settings_file.write_text("{ not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=pig_settings.__name__):
        r = client.get("/settings")
    assert r.status_code == 200
    assert r.json()["building"]["length_m"] == 100.0       # умолчания — интерфейс жив
    assert "unreadable" in caplog.text                       # но причина видна в журнале


def test_settings_write_needs_token_when_configured(client, settings_file, monkeypatch):
    monkeypatch.setenv("WRITE_TOKEN", "farm-secret")
    r = client.put("/settings", json=SETTINGS)
    assert r.status_code == 401 and r.json()["detail"] == "write_token_required"
    assert not settings_file.exists()                        # отказ раньше записи
    assert client.put("/settings", json=SETTINGS, headers={"X-Write-Token": "farm-secret"}).status_code == 200
    # Чтение и расчёты токена не требуют
    assert client.get("/settings").status_code == 200
    assert client.post("/calc/ventilation", json=VENT).status_code == 200


def test_settings_write_open_without_token(client):
    # Регрессионный сторож: локально и без WRITE_TOKEN — как раньше
    assert client.put("/settings", json=SETTINGS).status_code == 200


# ─── Проверка входа ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("season", ["Winter", "зима", ""])
def test_unknown_season_is_rejected(client, season):
    # Раньше всё, что не «winter», молча считалось летом
    assert client.post("/calc/ventilation", json={**VENT, "season": season}).status_code == 422


def test_season_picks_its_setpoint(client):
    # PIC табл. 4.1, 34 кг: зимняя 19.0, летняя 18.0
    assert client.post("/calc/ventilation", json={**VENT, "season": "winter"}).json()["setpoint_c"] == 19.0
    assert client.post("/calc/ventilation", json={**VENT, "season": "summer"}).json()["setpoint_c"] == 18.0


def test_unknown_floor_type_is_a_validation_error(client):
    r = client.post("/calc/stocking", json={"weight_kg": 50, "num_pigs": 100,
                                            "floor_area_m2": 80, "floor_type": "mesh"})
    assert r.status_code == 422


# ─── Вес вне таблицы PIC 4.1 ──────────────────────────────────────────────────

@pytest.mark.parametrize("weight", [160.0, 3.0])
def test_weight_outside_table_warns(client, weight):
    for path, extra in (("/calc/ventilation", {}), ("/calc/heat-balance", {"q_min_m3h": 5000})):
        w = client.post(path, json={**VENT, **extra, "weight_kg": weight}).json()["warnings"]
        # Наличие своего кода, а не точный состав: модель тепла добавляет свои
        # предупреждения (за 120 кг — feed_level_extrapolated)
        table = [x for x in w if x["code"] == "weight_outside_table"]
        assert table and table[0]["params"] == {"weight_kg": weight, "table_min_kg": 5.4, "table_max_kg": 147.0}


def test_weight_inside_table_has_no_warning(client):
    assert client.post("/calc/ventilation", json=VENT).json()["warnings"] == []


# ─── Газы: ppm, как в оригинале PIC ───────────────────────────────────────────

def test_gas_limits_are_ppm_with_mg_conversion(client):
    g = client.post("/calc/ventilation", json=VENT).json()["gas_limits"]
    assert g["ppm"] == {"nh3": 20, "co2": 3000, "co": 30, "h2s": 5}
    # мг/м³ = ppm × M / 24.04 (20 °C): NH₃ 20 × 17.031 / 24.04 = 14.2
    assert g["mg_m3_at_20c"]["nh3"] == pytest.approx(14.2, abs=0.05)
    assert g["mg_m3_at_20c"]["co2"] == pytest.approx(5492.1, abs=0.5)
    assert g["mg_m3_at_20c"]["h2s"] == pytest.approx(7.1, abs=0.05)


def test_ui_shows_gas_limits_in_ppm(client):
    html = client.get("/").text
    assert "20 ppm" in html and "mg/m³" not in html


# ─── Обратная связь ───────────────────────────────────────────────────────────

def test_feedback_is_plain_text_under_own_name(client, monkeypatch):
    sent = []

    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout):
        sent.append(json.loads(req.data))
        return _Resp()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setattr(feedback.urllib.request, "urlopen", fake_urlopen)
    r = client.post("/feedback", json={"message": "CO2_max *странно", "page": "calc_v2"})
    assert r.status_code == 200
    body = sent[0]
    assert "parse_mode" not in body                  # «_» и «*» посетителя не ломают доставку
    assert body["text"].startswith("📬 PigMaxApp Feedback")
    assert "CO2_max *странно" in body["text"]
