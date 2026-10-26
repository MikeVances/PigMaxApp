"""
Пункт 4: тепловой стресс растущих свиней по Huynh 2005 (аннотация PMID 15890816).

Числа в тестах — прямо из аннотации: пороги 21.3 / 22.9 / 24.6 / 25.5 °C,
потеря корма 81 / 99 / 106 г/(гол·сут) на °C при RH 50 / 65 / 80 %.
"""
from __future__ import annotations

import pytest

from app.core.heat_stress import feed_loss_slope_g_per_c, heat_stress, rh_at
from app.core.psychro import humidity_ratio

# Максимум уравнения Quiniou & Noblet по T: 1213 / (2 · 31.5) = 19.254 °C
PEAK = 1213 / 63

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


def test_lactating_sows_use_quiniou_noblet(client):
    body = {"animals": {"group": "lactating_sow", "weight_kg": 220, "count": 40, "lactation_day": 10},
            "conditions": {"t_outside_c": 30, "rh_outside_pct": 50, "t_inside_c": 27}}
    j = client.post("/api/v1/ventilation-requirements", json=body).json()
    hs = j["heat_stress"]
    # 27 °C: потеря против максимума уравнения 31.5 · (27 − 1213/63)² ≈ 1890 г/сут
    assert hs["model"] == "quiniou_noblet_1999" and hs["feed_loss_g_day"] == round(31.5 * (27 - PEAK) ** 2)
    assert hs["exceeded"] == ["respiration", "litter_growth"]
    assert any(w["code"] == "heat_stress_sow_litter" for w in j["warnings"])
    assert "quiniou_noblet_1999" in [s["id"] for s in j["sources"]]


def test_gestating_sows_have_no_heat_stress(client):
    # Опыт Quiniou & Noblet — только лактация: на супоросных числа не переносим
    body = {"animals": {"group": "gestating_sow", "weight_kg": 220, "count": 40, "pregnancy_day": 60},
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



@pytest.mark.parametrize("t,exceeded,loss", [
    (19.0, [], 0),                                    # ниже максимума уравнения — потерь нет
    (22.0, ["respiration"], round(31.5 * (22 - PEAK) ** 2)), # «ниже 22» — при 22 реакция уже есть
    (25.0, ["respiration"], round(31.5 * (25 - PEAK) ** 2)), # рост поросят — строго выше 25
    (25.5, ["respiration", "litter_growth"], round(31.5 * (25.5 - PEAK) ** 2)),
])
def test_sow_thresholds_and_loss(t, exceeded, loss):
    from app.core.heat_stress import lactating_sow_heat_stress
    r = lactating_sow_heat_stress(t, "air", [], [])
    assert (r["exceeded"], r["feed_loss_g_day"]) == (exceeded, loss)


def test_sow_loss_matches_the_equation():
    # Потеря = VFI(19.25) − VFI(T) по полному уравнению аннотации при любом весе
    from app.core.heat_stress import lactating_sow_heat_stress
    vfi = lambda t, bw: -49052 + 1213 * t - 31.5 * t * t + 330 * bw - 0.61 * bw * bw
    for bw in (180, 250):
        assert lactating_sow_heat_stress(28.0, "air", [], [])["feed_loss_g_day"] == round(vfi(19.25, bw) - vfi(28.0, bw))


def test_sow_beyond_study_is_marked():
    from app.core.heat_stress import lactating_sow_heat_stress
    a = []
    lactating_sow_heat_stress(31.0, "air", a, [])
    assert a == [{"code": "heat_stress_beyond_study_range", "params": {"t_c": 31.0, "study_max_c": 29.0}}]


def test_effective_tool_for_sows(client):
    j = client.post("/calc/effective-temperature", json={"t_air_c": 27, "air_speed_ms": 0.2, "group": "lactating_sow"}).json()
    assert j["heat_stress"]["model"] == "quiniou_noblet_1999" and j["heat_stress"]["t_c"] == j["t_effective_c"]
