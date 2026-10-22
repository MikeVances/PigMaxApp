"""
Этап 8.1: утечки и конденсат в сценариях (перенесено из MINMAXapp; паритет —
test_house_parity.py). Здесь — что они реально меняют ответ, а не просто есть в коде.
"""
from __future__ import annotations

import pytest

NOW = "/api/v1/ventilation-requirements"
DESIGN = "/api/v1/ventilation-designs"

# Малое стадо в большом корпусе: минимум мал, щели решают
NOW_LEAKY = {"animals": {"group": "growing", "count": 100, "genetics": "pic_2019", "day": 10},
             "conditions": {"t_outside_c": -20, "rh_outside_pct": 85},
             "house": {"length_m": 60, "width_m": 18, "wall_height_m": 2.6,
                       "envelope": {"ua_w_per_k": 600}, "airtightness": {"class": "very_leaky"}}}


def test_leak_raises_heating_when_it_exceeds_minimum(client):
    tight = client.post(NOW, json={**NOW_LEAKY, "house": {**NOW_LEAKY["house"], "airtightness": {"class": "tight"}}}).json()
    leaky = client.post(NOW, json=NOW_LEAKY).json()
    # very_leaky: 1.2 м²/1000 м² × 1080 м² = 1.296 м² ELA; Q = 1.296·√(2·4/1.184)·3600 = 12 128 м³/ч
    assert leaky["heating"]["leak_4pa_m3h"] == pytest.approx(12128, abs=2)
    assert leaky["heating"]["air_exchange_basis"] == "leakage"
    assert leaky["heating"]["heater_required_kw"] > tight["heating"]["heater_required_kw"]
    assert "leakage_exceeds_min_vent" in {w["code"] for w in leaky["warnings"]}


def test_no_geometry_no_leakage(client):
    body = {**NOW_LEAKY, "house": {"envelope": {"ua_w_per_k": 600}}}
    j = client.post(NOW, json=body).json()
    assert "leak_4pa_m3h" not in j["heating"] and j["heating"]["air_exchange_basis"] == "min_ventilation"


def test_default_airtightness_is_an_assumption(client):
    body = {**NOW_LEAKY, "house": {k: v for k, v in NOW_LEAKY["house"].items() if k != "airtightness"}}
    j = client.post(NOW, json=body).json()
    assert {"code": "airtightness_class_assumed", "params": {"class": "moderate", "relative_leakage": 0.65}} in j["assumptions"]


def test_condensation_on_a_cold_wall(client):
    # Однослойный кирпич 250 мм: U = 1/(0.13 + 0.25/0.77 + 0.04) = 2.02; при −20 снаружи
    # и 24 °C внутри поверхность 24 − 2.02·0.13·44 = 12.4 °C — ниже росы при 70 % (18.2)
    body = {"animals": {"group": "growing", "count": 500, "weight_kg": 15},
            "conditions": {"t_outside_c": -20, "rh_outside_pct": 85},
            "house": {"length_m": 60, "width_m": 18, "wall_height_m": 2.6,
                      "envelope": {"walls": {"layers": [{"material_id": "brick_exposed", "thickness_mm": 250}]},
                                   "roof": {"u_w_m2k": 0.25}}},
            "model": {"rh_max_pct": 70}}
    j = client.post(NOW, json=body).json()
    cond = next(w for w in j["warnings"] if w["code"] == "condensation_risk")
    assert cond["params"]["element"] in ("side_walls", "end_walls")
    assert cond["params"]["surface_c"] == pytest.approx(12.4, abs=0.2)
    assert cond["params"]["surface_c"] < cond["params"]["dew_point_c"]


def test_warm_panels_no_condensation(client):
    body = {"animals": {"group": "growing", "count": 500, "weight_kg": 60},
            "conditions": {"t_outside_c": -20, "rh_outside_pct": 85},
            "house": {"length_m": 60, "width_m": 18, "wall_height_m": 2.6,
                      "envelope": {"walls": {"u_w_m2k": 0.25}, "roof": {"u_w_m2k": 0.22}}}}
    assert "condensation_risk" not in {w["code"] for w in client.post(NOW, json=body).json()["warnings"]}


def test_design_reports_leakage_days_and_basis(client):
    body = {"house": {"length_m": 60, "width_m": 18, "wall_height_m": 2.6, "envelope": {"ua_w_per_k": 600},
                      "airtightness": {"class": "very_leaky"}},
            "herd": {"genetics": "pic_2019", "count": 150, "start_day": 1, "end_day": 120, "step_days": 30},
            "climate": {"winter_design_c": -25, "summer_design_db_c": 30, "summer_design_rh_pct": 40}}
    j = client.post(DESIGN, json=body).json()
    leak = next(w for w in j["warnings"] if w["code"] == "leakage_exceeds_min_vent")
    assert leak["params"]["days"] == [d["day"] for d in j["days"] if d["heating_air_basis"] == "leakage"]
    assert j["envelope"]["leak_4pa_m3h"] == pytest.approx(12128, abs=2)


def test_leakage_tool(client):
    # UGA: 36 000 м³/ч при 25 Па, тест 30 Па → поправка, ELA, класс
    j = client.post("/calc/leakage", json={"house_length_m": 96, "house_width_m": 18,
                                          "fan_capacity_m3h": 36000, "measured_pressure_pa": 30}).json()
    assert j["tightness_class"] in ("tight", "moderate", "leaky", "very_leaky") and j["ela_m2"] > 0
    r = client.post("/calc/leakage", json={"house_length_m": 96, "house_width_m": 18, "fan_capacity_m3h": 36000,
                                          "measured_pressure_pa": 30, "extra_points": [[35000, 31]]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "leakage_points_too_close"
