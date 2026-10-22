"""
Этап 5: свиноматки — тепло по Stinn & Xin 2014, уставки помещения и гнезда,
помещение свиноматок целиком (/calc/sow-room).

Эталоны — числа таблиц источников и ручная арифметика в комментариях.
"""
from __future__ import annotations

import pytest

from backend.app.core import sources
from backend.app.core import sow_climate as SC
from backend.app.core.pig_heat import MethodNotApplicable, pig_heat


# ─── Stinn & Xin 2014 ─────────────────────────────────────────────────────────

def test_early_gestation_at_20c():
    # Табл. 3, B/EG 20 °C: 1.89 / 0.74 / 1.15 Вт/кг × 204 кг
    h = pig_heat("gestating_sow", 204, 20, method="stinn_xin_2014", pregnancy_day=20)
    assert (h.total_w, h.sensible_w, h.latent_w) == (385.6, 234.6, 151.0)
    # CO₂ по ур. 1–2, RQ 1.07: 385.56 / (16.18/1.07 + 5.02) = 19.143 мл/с = 68.9 л/ч
    assert h.co2_l_h == pytest.approx(68.9, abs=0.05)


def test_late_gestation_interpolates_temperature():
    # LG, 22.5 °C — середина 20 и 25: (1.57 + 1.26)/2 = 1.415 × 219 = 309.9 Вт
    h = pig_heat("gestating_sow", 219, 22.5, method="stinn_xin_2014", pregnancy_day=80)
    assert h.total_w == pytest.approx(309.9, abs=0.05)
    assert {"code": "gestation_stage", "params": {"stage": "LG"}} in h.assumptions


def test_lactation_grows_with_the_litter():
    # Табл. 4, на кг свиноматки С ГНЕЗДОМ: дни 0–6 — 3.35 × 235 (масса из таблицы)
    week0 = pig_heat("lactating_sow", 223, 22, method="stinn_xin_2014", lactation_day=3)
    assert week0.total_w == pytest.approx(787.2, abs=0.05)
    assert {"code": "sow_and_litter_mass_from_source", "params": {"mass_kg": 235.0}} in week0.assumptions
    # Дни 13–18 с гнездом 33 кг: 4.38 × (223 + 33) = 1121.3 Вт
    late = pig_heat("lactating_sow", 223, 22, method="stinn_xin_2014", lactation_day=15, litter_mass_kg=33)
    assert late.total_w == pytest.approx(1121.3, abs=0.05)


def test_stinn_xin_matches_rd_apk_and_beats_old_cigr():
    # Независимое согласие: РД-АПК (200 кг, 22 °C) 788.8 Вт; CIGR с 6 кг молока — 434
    sx = pig_heat("lactating_sow", 223, 22, method="stinn_xin_2014", lactation_day=3).total_w
    rd = pig_heat("lactating_sow", 200, 22, method="rd_apk_2012").total_w
    cigr = pig_heat("lactating_sow", 220, 22).total_w
    assert sx == pytest.approx(rd, rel=0.01)
    assert cigr < 0.6 * sx


@pytest.mark.parametrize("group,kw,exc", [("lactating_sow", {}, ValueError),
                                          ("boar", {}, MethodNotApplicable),
                                          ("growing", {}, MethodNotApplicable)])
def test_stinn_xin_scope(group, kw, exc):
    with pytest.raises(exc):
        pig_heat(group, 220, 20, method="stinn_xin_2014", **kw)


# ─── Уставки свиноматок ───────────────────────────────────────────────────────

def test_pic_farrowing_room_drops_to_19():
    # Табл. 5.2: 21–23 при опоросе → 19 к 7–10-му дню (середины: 22 и 8.5)
    at = lambda d: SC.sow_setpoints("pic_2021", "lactation", d).room_setpoint_c
    assert (at(0), at(8.5), at(20)) == (22.0, 19.0, 19.0)
    assert SC.sow_setpoints("pic_2021", "gestation").room_setpoint_c == 19.0


def test_nl_is_ventilation_start_and_covered_nest():
    s = SC.sow_setpoints("klimaatplatform_2021", "lactation", 7)
    assert (s.room_setpoint_c, s.design_room_temp_c) == (20.0, 18.5)
    assert SC.sow_setpoints("klimaatplatform_2021", "lactation", 7, covered_nest=True).room_setpoint_c == 18.0
    # Мин. вентиляция на свиноматку: день 7 — 35, 25 — 60; 16-й: 35 + 25·9/18 = 47.5
    assert SC.sow_setpoints("klimaatplatform_2021", "lactation", 16).min_vent_m3h == 47.5
    assert SC.sow_setpoints("klimaatplatform_2021", "dry").min_vent_m3h == 18.0


def test_rd_apk_nest_falls_to_22_at_weaning():
    # Прим. 8: 30 °C → 22 к отъёму. Отъём 28 (по умолчанию): день 14 — 26; отъём 21: 30 − 8·14/21 = 24.7
    assert SC.sow_setpoints("rd_apk_2012", "lactation", 14).nest_c == 26.0
    assert SC.sow_setpoints("rd_apk_2012", "lactation", 14, weaning_day=21).nest_c == 24.7
    assert SC.sow_setpoints("rd_apk_2012", "gestation").room_setpoint_c == 16.0


def test_hypor_nest_and_room():
    s = SC.sow_setpoints("hypor_2023", "lactation", 0)
    assert (s.room_setpoint_c, s.nest_c) == (23.5, 34.5)
    with pytest.raises(ValueError):
        SC.sow_setpoints("hypor_2023", "dry")          # в руководстве по опоросу этого нет


@pytest.mark.parametrize("pid", list(SC.SOW_PROFILES))
def test_sow_profiles_sourced(pid):
    assert sources.get_source(SC.SOW_PROFILES[pid].source_id) is not None


# ─── Помещение свиноматок ─────────────────────────────────────────────────────

ROOM = dict(profile="pic_2021", stage="lactation", lactation_day=7, num_sows=40, sow_weight_kg=223,
            t_outside_c=-15, rh_outside_pct=85, ua_w_per_k=400, heater_power_kw=200, heater_type="electric")


def test_sow_room_minimum_is_the_largest_criterion(client):
    j = client.post("/calc/sow-room", json=ROOM).json()
    mv = j["min_ventilation"]
    present = [v for v in (mv["co2_m3h"], mv["moisture_m3h"], mv["norm_m3h"]) if v is not None]
    assert mv["per_sow_m3h"] == max(present)
    assert mv["total_m3h"] == pytest.approx(mv["per_sow_m3h"] * 40, abs=1)


def test_creep_heat_stays_in_the_room(client):
    # 40 станков × 150 Вт = 6 кВт уходят в помещение — потребность в отоплении на 6 кВт меньше
    without = client.post("/calc/sow-room", json=ROOM).json()["heating"]
    with_creep = client.post("/calc/sow-room", json={**ROOM, "creep_heat_w_per_pen": 150}).json()["heating"]
    assert without["heater_required_kw"] - with_creep["heater_required_kw"] == pytest.approx(6.0, abs=0.02)
    assert with_creep["creep_heat_kw"] == 6.0


def test_creep_heat_absence_is_flagged(client):
    codes = {a["code"] for a in client.post("/calc/sow-room", json=ROOM).json()["assumptions"]}
    assert "creep_heat_not_counted" in codes


@pytest.mark.parametrize("norm,expected", [("pic", 34.0), ("profile", None)])
def test_sow_room_norms(client, norm, expected):
    j = client.post("/calc/sow-room", json={**ROOM, "norm": norm}).json()
    assert j["min_ventilation"]["norm_m3h"] == expected   # у профиля PIC своей нормы на свиноматку нет


def test_pre_farrowing_with_cigr_is_late_gestation(client):
    j = client.post("/calc/sow-room", json={**ROOM, "lactation_day": -2, "method": "cigr2002"}).json()
    assert j["heat_per_sow"]["group"] == "gestating_sow"
    assert {"code": "pre_farrowing_as_late_gestation", "params": {"pregnancy_day": 112.0}} in j["assumptions"]


@pytest.mark.parametrize("patch", [{"lactation_day": None}, {"profile": "nope"},
                                   {"profile": "hypor_2023", "stage": "dry"},
                                   {"stage": "gestation", "lactation_day": None}])
def test_sow_room_validation(client, patch):
    assert client.post("/calc/sow-room", json={**ROOM, **patch}).status_code == 422


def test_sow_room_sources_resolve(client):
    for prof in SC.SOW_PROFILES:
        stage = "lactation" if prof == "hypor_2023" else "gestation"
        body = {**ROOM, "profile": prof, "stage": stage, "pregnancy_day": 80}
        j = client.post("/calc/sow-room", json=body).json()
        for sid in j["sources"]:
            assert client.get(f"/sources/{sid}").status_code == 200, sid
