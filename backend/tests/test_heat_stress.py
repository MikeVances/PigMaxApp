"""
Пункт 4: тепловой стресс растущих свиней по Huynh 2005 (аннотация PMID 15890816).

Числа в тестах — прямо из аннотации: пороги 21.3 / 22.9 / 24.6 / 25.5 °C,
потеря корма 81 / 99 / 106 г/(гол·сут) на °C при RH 50 / 65 / 80 %.
"""
from __future__ import annotations

import pytest

from app.core.heat_stress import feed_loss_slope_g_per_c, heat_stress, rh_at
from app.core.psychro import humidity_ratio

NOW = {"animals": {"group": "growing", "weight_kg": 65, "count": 100},
       "conditions": {"t_outside_c": 30, "rh_outside_pct": 50, "t_inside_c": 29}}
DESIGN = {"house": {"length_m": 60, "width_m": 18, "wall_height_m": 2.6, "envelope": {"ua_w_per_k": 600}},
          "herd": {"genetics": "pic_2019", "count": 1000, "start_day": 30, "end_day": 150, "step_days": 30},
          "climate": {"winter_design_c": -25, "summer_design_db_c": 32, "summer_design_rh_pct": 40}}


@pytest.mark.parametrize("rh,slope", [(50, 81), (65, 99), (80, 106), (57.5, 90), (72.5, 102.5), (30, 81), (95, 106)])
def test_feed_loss_slope_by_rh(rh, slope):
    # Точки опыта, середины между ними — линейно, вне 50–80 % — край
    assert feed_loss_slope_g_per_c(rh) == pytest.approx(slope)


def test_thresholds_and_feed_loss():
    a, w = [], []
    r = heat_stress(27.5, 65, 65, "air", a, w)
    assert r["exceeded"] == ["respiration", "heat_production", "rectal_temperature", "feed_intake"]
    assert r["feed_loss_g_day"] == 198                     # 2 °C × 99 г
    assert w == [{"code": "heat_stress_feed_intake", "params": {"t_c": 27.5, "threshold_c": 25.5, "feed_loss_g_day": 198}}]
    assert a == []                                         # вес и RH в пределах опыта


def test_below_feed_threshold_only_respiration():
    a, w = [], []
    r = heat_stress(22.0, 65, 65, "effective", a, w)
    assert r["exceeded"] == ["respiration"] and r["feed_loss_g_day"] == 0 and w == []


def test_outside_study_is_marked():
    a, w = [], []
    heat_stress(34.0, 40, 120, "air", a, w)
    codes = {n["code"] for n in a}
    assert codes == {"heat_stress_weight_extrapolated", "heat_stress_rh_outside_study", "heat_stress_beyond_study_range"}


def test_rh_at_inverts_humidity_ratio():
    assert rh_at(25.0, humidity_ratio(25.0, 63.0)) == pytest.approx(63.0, abs=1e-6)


def test_now_scenario_still_air(client):
    j = client.post("/api/v1/ventilation-requirements", json=NOW).json()
    hs = j["heat_stress"]
    # Без скорости — воздух помещения 29 °C; RH — предел профиля (заметка)
    assert hs["temperature_basis"] == "air" and hs["t_c"] == 29.0
    assert "feed_intake" in hs["exceeded"] and hs["feed_loss_g_day"] > 0
    assert any(n["code"] == "heat_stress_rh_is_limit" for n in j["assumptions"])
    assert any(n["code"] == "heat_stress_feed_intake" for n in j["warnings"])
    assert "huynh_2005" in [s["id"] for s in j["sources"]]


def test_now_scenario_air_speed_uses_effective_temperature(client):
    body = {**NOW, "conditions": {**NOW["conditions"], "air_speed_ms": 0.5}}
    j = client.post("/api/v1/ventilation-requirements", json=body).json()
    assert j["heat_stress"]["temperature_basis"] == "effective"
    assert j["heat_stress"]["t_c"] == j["effective_temperature"]["t_effective_c"]
    assert j["heat_stress"]["exceeded"] == ["respiration"]


def test_sows_have_no_heat_stress(client):
    body = {"animals": {"group": "lactating_sow", "weight_kg": 220, "count": 40, "lactation_day": 7},
            "conditions": {"t_outside_c": 30, "rh_outside_pct": 50, "t_inside_c": 29}}
    r = client.post("/api/v1/ventilation-requirements", json=body)
    assert r.status_code == 201 and "heat_stress" not in r.json()


def test_design_pads_raise_humidity(client):
    # За панелями влагосодержание растёт по линии мокрого термометра: RH у свиней
    # заметно выше наружных 40 % — с наружным влагосодержанием вышло бы ~60 %
    j = client.post("/api/v1/ventilation-designs", json={**DESIGN, "pads": {"pad_type": "7090", "thickness_mm": 150}}).json()
    hs = j["equipment"]["heat_stress"]
    assert hs["t_c"] == j["equipment"]["summer_air_c"] and hs["temperature_basis"] == "air"
    assert hs["rh_pct"] >= 75


def test_design_without_pads(client):
    j = client.post("/api/v1/ventilation-designs", json=DESIGN).json()
    hs = j["equipment"]["heat_stress"]
    assert hs["t_c"] == j["equipment"]["summer_air_c"] and hs["rh_pct"] < 40   # наружный воздух нагрет — RH ниже
    assert hs["feed_loss_g_day"] > 0
    assert any(n["code"] == "heat_stress_beyond_study_range" for n in j["assumptions"])


def test_rectal_threshold_is_lower_bound():
    # 25.0 °C: ректальная температура (нижняя граница 24.6) уже, корм (25.5) ещё нет
    r = heat_stress(25.0, 65, 65, "air", [], [])
    assert r["exceeded"] == ["respiration", "heat_production", "rectal_temperature"]


def test_effective_temperature_tool_adds_heat_stress(client):
    base = {"t_air_c": 30, "air_speed_ms": 0.2}
    assert "heat_stress" not in client.post("/calc/effective-temperature", json=base).json()
    j = client.post("/calc/effective-temperature", json={**base, "rh_pct": 70, "weight_kg": 65}).json()
    assert j["heat_stress"]["t_c"] == j["t_effective_c"] and j["heat_stress"]["temperature_basis"] == "effective"
    assert "huynh_2005" in j["sources"]


def test_tools_page_is_served(client):
    html = client.get("/tools.html").text
    assert 'id="fRun"' in html and "tools.js" in html
