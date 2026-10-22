"""
Этап 6б: сценарии API v1 — «потребность сейчас» и «проект».

Главный приём — перекрёстная сверка путей: то же число, посчитанное сценарием и
напрямую функциями ядра этапов 1–5, обязано совпасть. Так проверяется, что
сценарий передаёт в ядро то, что пришло в запросе, а не что-то по умолчанию.
"""
from __future__ import annotations

import json

import pytest

from backend.app import api_v1
from backend.app.core import genetics as G
from backend.app.core.building_thermal import calculate_heat_balance
from backend.app.core.min_ventilation import min_ventilation

NOW = "/api/v1/ventilation-requirements"
DESIGN = "/api/v1/ventilation-designs"

NOW_REQ = {"animals": {"group": "growing", "count": 1000, "genetics": "pic_2019", "day": 100},
           "conditions": {"t_outside_c": -10, "rh_outside_pct": 90}}
DESIGN_REQ = {
    "house": {"length_m": 60, "width_m": 18, "wall_height_m": 2.6, "ridge_height_m": 4.0,
              "envelope": {"ua_w_per_k": 600}},
    "herd": {"genetics": "pic_2019", "count": 1000, "start_day": 30, "end_day": 150, "step_days": 30},
    "climate": {"winter_design_c": -25, "summer_design_db_c": 32, "summer_design_rh_pct": 40}}


def _audit(rid: str) -> dict:
    for line in api_v1.AUDIT_LOG_PATH.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("request_id") == rid:
            return e
    raise AssertionError(rid)


# ─── «Сейчас» ─────────────────────────────────────────────────────────────────

def test_now_uses_profile_weight_and_setpoint(client):
    # PIC табл. 4.1: день 100 — 72 кг, нужная температура 16.0
    j = client.post(NOW, json=NOW_REQ).json()
    assert (j["weight_kg"], j["t_inside_c"]) == (72.0, 16.0)
    assert j["setpoints"]["setpoint_meaning"] == "room_temperature"


def test_now_minimum_equals_the_core(client):
    j = client.post(NOW, json=NOW_REQ).json()
    core = min_ventilation("growing", 72.0, 16.0, -10, 90, rh_max_pct=65.0, norm="pic",
                           genetics="pic_2019")
    assert j["min_ventilation"]["per_head_m3h"] == core.per_head_m3h
    assert j["min_ventilation"]["total_m3h"] == pytest.approx(core.per_head_m3h * 1000, abs=1)
    assert j["min_ventilation"]["basis"] == core.basis


def test_now_ventilation_start_profile_designs_lower(client):
    body = {**NOW_REQ, "animals": {"group": "growing", "count": 500, "genetics": "klimaatplatform_2021",
                                   "weight_kg": 66.22, "phase": "finisher"}}
    j = client.post(NOW, json=body).json()
    assert j["setpoints"]["setpoint_c"] == 20.0 and j["t_inside_c"] == 18.5


def test_now_given_inside_temperature_wins(client):
    body = {**NOW_REQ, "conditions": {**NOW_REQ["conditions"], "t_inside_c": 18}}
    j = client.post(NOW, json=body).json()
    assert j["t_inside_c"] == 18.0
    assert {"code": "inside_temperature_given", "params": {"t_inside_c": 18.0}} in j["assumptions"]


def test_now_heating_only_with_envelope_or_heaters(client):
    assert "heating" not in client.post(NOW, json=NOW_REQ).json()
    j = client.post(NOW, json={**NOW_REQ, "house": {"envelope": {"ua_w_per_k": 1500}}}).json()
    # Перекрёстная сверка с перенесённым балансом: те же входы — та же мощность
    core = calculate_heat_balance(1500, j["min_ventilation"]["total_m3h"], 16.0, -10, 1000, 72.0, 0.0,
                                  animals_sensible_w=j["heat"]["sensible_w"] * 1000)
    assert j["heating"]["heater_required_kw"] == core.q_heater_required_kw


def test_now_sow_room_with_creep_heat(client):
    body = {"animals": {"group": "lactating_sow", "count": 40, "weight_kg": 223, "lactation_day": 7,
                        "sow_profile": "pic_2021"},
            "conditions": {"t_outside_c": -15, "rh_outside_pct": 85},
            "house": {"envelope": {"ua_w_per_k": 400}}, "heaters": {"power_kw": 200, "type": "electric"},
            "model": {"method": "stinn_xin_2014"}}
    without = client.post(NOW, json=body).json()["heating"]["heater_required_kw"]
    with_creep = client.post(NOW, json={**body, "creep_heat_w_per_pen": 150}).json()
    # 40 × 150 Вт = 6 кВт тепла гнёзд остаются в помещении
    assert without - with_creep["heating"]["heater_required_kw"] == pytest.approx(6.0, abs=0.02)
    assert with_creep["setpoints"]["nest_c"] == 33.5


def test_now_is_in_the_audit_log(client):
    j = client.post(NOW, json=NOW_REQ).json()
    e = _audit(j["request_id"])
    assert e["status"] == 201 and e["result"]["min_total_m3h"] == j["min_ventilation"]["total_m3h"]


@pytest.mark.parametrize("animals,code", [
    ({"group": "growing", "count": 10}, "validation_failed"),
    ({"group": "lactating_sow", "count": 10, "weight_kg": 220}, "validation_failed"),
    ({"group": "growing", "count": 10, "genetics": "nope", "day": 10}, "genetics_not_found"),
])
def test_now_validation(client, animals, code):
    r = client.post(NOW, json={**NOW_REQ, "animals": animals})
    assert r.json()["code"] == code


# ─── «Проект» ─────────────────────────────────────────────────────────────────

def test_design_days_grid_and_mortality(client):
    j = client.post(DESIGN, json={**DESIGN_REQ, "herd": {**DESIGN_REQ["herd"], "mortality_pct": 3}}).json()
    assert [d["day"] for d in j["days"]] == [30, 60, 90, 120, 150]
    # Постоянный суточный падёж (survival MINMAXapp): на последнем дне ровно 1000 · (1 − 0.03)
    assert j["days"][0]["pigs"] == 1000 and j["days"][-1]["pigs"] == 970
    # День 90 — середина периода 30…150 (121 день): 0.97^(60/120) = 0.98489 → 985
    assert next(d for d in j["days"] if d["day"] == 90)["pigs"] == round(1000 * 0.97 ** (60 / 120))
    assert {"code": "mortality_constant_daily", "params": {"mortality_pct": 3.0}} in j["assumptions"]


def test_design_day_equals_the_core(client):
    # День 90: вес по кривой PIC, минимум и отопление — теми же функциями ядра
    j = client.post(DESIGN, json=DESIGN_REQ).json()
    day = next(d for d in j["days"] if d["day"] == 90)
    sp = G.setpoints("pic_2019", day=90)
    mv = min_ventilation("growing", sp.weight_kg, sp.design_room_temp_c, -25, 85, rh_max_pct=65.0,
                         norm="pic", genetics="pic_2019", phase=sp.phase)
    hb = calculate_heat_balance(600, mv.per_head_m3h * 1000, sp.design_room_temp_c, -25, 1000, sp.weight_kg, 0.0,
                                animals_sensible_w=mv.heat.sensible_w * 1000)
    assert day["weight_kg"] == round(sp.weight_kg, 1)
    assert day["min_per_head_m3h"] == mv.per_head_m3h
    assert day["heating_kw"] == hb.q_heater_required_kw


def test_design_equipment_is_the_worst_day(client):
    j = client.post(DESIGN, json=DESIGN_REQ).json()
    eq, days = j["equipment"], j["days"]
    assert eq["min_ventilation_capacity_m3h"] == max(d["min_ventilation_m3h"] for d in days)
    assert eq["max_ventilation_capacity_m3h"] == max(d["summer_ventilation_m3h"] for d in days)
    # Потребность — худший день; мощность к установке — с запасом 15 % (MINMAXapp)
    assert eq["heating_need_kw"] == max(d["heating_kw"] for d in days)
    assert eq["heating_capacity_kw"] == round(eq["heating_need_kw"] * 1.15, 1)
    assert {"code": "heating_safety_margin", "params": {"pct": 15.0}} in j["assumptions"]
    worst = max(days, key=lambda d: d["heating_kw"])
    assert eq["worst_heating_day"] == worst["day"]


def test_design_pads_judged_against_summer_target(client):
    # Летняя цель 26 °C (РД-АПК), а не зимняя уставка PIC 14.5: за панелями 24.8 — «на пределе»
    j = client.post(DESIGN, json={**DESIGN_REQ, "pads": {"pad_type": "7090", "thickness_mm": 150}}).json()
    pads = j["equipment"]["pads"]
    assert pads["t_at_fans_c"] < 26 and pads["status_code"] in ("ok", "cooling_marginal")
    assert j["equipment"]["summer_air_c"] == pads["t_at_fans_c"]
    assert "inside_above_rd_apk_limit" not in {w["code"] for w in j["warnings"]}


def test_design_without_pads_needs_speed(client):
    # 32 + 3 = 35 °C в корпусе; до 26 — 9 °C охлаждения: 0.457 + 1.067·2/3 = 1.17 м/с > 1.0 РД-АПК
    j = client.post(DESIGN, json=DESIGN_REQ).json()
    assert j["equipment"]["speed_to_summer_target_ms"] == pytest.approx(1.17, abs=0.01)
    assert {"code": "air_speed_above_rd_apk", "params": {"speed_ms": 1.17, "limit_ms": 1.0}} in j["warnings"]


def test_design_construction_envelope(client):
    body = {**DESIGN_REQ, "house": {**DESIGN_REQ["house"],
                                    "envelope": {"walls": {"u_w_m2k": 0.25}, "roof": {"u_w_m2k": 0.22}}}}
    env = client.post(DESIGN, json=body).json()["envelope"]
    assert env["basis"] == "construction" and env["ua_w_per_k"] > 0


def test_design_needs_growth_curve(client):
    r = client.post(DESIGN, json={**DESIGN_REQ, "herd": {**DESIGN_REQ["herd"], "genetics": "danbred"}})
    assert r.status_code == 422 and r.json()["code"] == "invalid_parameters"


def test_design_is_in_the_audit_log(client):
    j = client.post(DESIGN, json=DESIGN_REQ).json()
    e = _audit(j["request_id"])
    assert e["result"]["heating_capacity_kw"] == j["equipment"]["heating_capacity_kw"]


def test_now_max_equals_min_in_heating_mode(client):
    # Приёмка агента 2026-10-22: при −15 °C «максимум» считался при внутренней −12 °C
    # и выдавал летнюю норму опоросника. В режиме отопления максимум = минимум
    body = {"animals": {"group": "lactating_sow", "count": 40, "weight_kg": 220, "lactation_day": 7},
            "conditions": {"t_outside_c": -15, "rh_outside_pct": 85}}
    j = client.post(NOW, json=body).json()
    assert j["max_ventilation"]["basis"] == "minimum"
    assert j["max_ventilation"]["per_head_m3h"] == j["min_ventilation"]["per_head_m3h"]
    assert j["max_ventilation"]["t_inside_c"] == j["t_inside_c"]
    assert any(a["code"] == "max_equals_min_in_heating" for a in j["assumptions"])


def test_now_max_by_heat_or_norm_in_summer(client):
    j = client.post(NOW, json={**NOW_REQ, "conditions": {"t_outside_c": 28, "rh_outside_pct": 50}}).json()
    assert j["max_ventilation"]["basis"] in ("heat", "norm")
    assert j["max_ventilation"]["per_head_m3h"] > j["min_ventilation"]["per_head_m3h"]


def test_design_heating_margin_is_a_parameter(client):
    j = client.post(DESIGN, json={**DESIGN_REQ, "heating_margin_pct": 0}).json()
    # Мощность к установке округлена до 0.1 кВт (как MINMAXapp), потребность — точнее
    assert j["equipment"]["heating_capacity_kw"] == round(j["equipment"]["heating_need_kw"], 1)


def test_gas_indirect_is_modelled_as_electric(client):
    # Теплообменник с дымоходом наружу: продуктов сгорания в воздухе корпуса нет —
    # ответ совпадает с электрическим и без воздуха разбавления горелок
    base = {**NOW_REQ, "house": {"envelope": {"ua_w_per_k": 600}}}
    ind = client.post(NOW, json={**base, "heaters": {"power_kw": 300, "type": "gas_indirect"}}).json()
    el = client.post(NOW, json={**base, "heaters": {"power_kw": 300, "type": "electric"}}).json()
    gas = client.post(NOW, json={**base, "heaters": {"power_kw": 300, "type": "gas_open"}}).json()
    assert ind["heating"] == el["heating"] and ind["heating"]["dilution_air_m3h"] == 0
    assert gas["heating"]["dilution_air_m3h"] > 0


def test_design_survival_is_constant_daily_not_linear(client):
    # Различающая проба: при 3 % линейная и экспоненциальная модели на середине
    # округляются в одно число (985). При 30 % и 100 000 голов — 85 000 против 83 666
    herd = {**DESIGN_REQ["herd"], "count": 100_000, "mortality_pct": 30}
    j = client.post(DESIGN, json={**DESIGN_REQ, "herd": herd}).json()
    mid = next(d for d in j["days"] if d["day"] == 90)
    assert mid["pigs"] == round(100_000 * 0.7 ** (60 / 120)) == 83666
