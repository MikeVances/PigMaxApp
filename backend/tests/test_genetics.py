"""
Этап 3: профили генетики (core/genetics.py).

Эталоны — числа первоисточников (docs/sources.md, раздел 2) и ручной пересчёт
дней в вес; арифметика в комментариях.
"""
from __future__ import annotations

import pytest

from backend.app.core import genetics as G
from backend.app.core import sources
from backend.app.core.min_ventilation import min_ventilation


# ─── Целостность данных ───────────────────────────────────────────────────────

@pytest.mark.parametrize("pid", list(G.PROFILES))
def test_profile_curves_are_ordered_and_sourced(pid):
    p = G.PROFILES[pid]
    assert sources.get_source(p.source_id) is not None
    for phase in (p.nursery, p.finisher):
        for curve in (phase.temp_by_weight, phase.min_vent_by_weight, phase.max_vent_by_weight, phase.rh_by_weight):
            if curve:
                xs = [x for x, _ in curve]
                assert xs == sorted(xs) and len(set(xs)) == len(xs), (pid, curve)
    if p.growth:
        days = [d for d, _ in p.growth]
        kgs = [k for _, k in p.growth]
        assert days == sorted(days)
        assert kgs == sorted(kgs), "вес не должен убывать по дням"


# ─── Смысл уставки ────────────────────────────────────────────────────────────

def test_room_temperature_profile_designs_at_setpoint():
    s = G.setpoints("pic_2019", weight_kg=98)
    assert s.setpoint_c == 14.5 and s.design_room_temp_c == 14.5


def test_ventilation_start_profile_designs_below_it():
    # Klimaatplatform: начало вентиляции; отопление на нейтральную зону 1–2 °C ниже → −1.5
    s = G.setpoints("klimaatplatform_2021", weight_kg=66.22, phase="finisher")
    assert s.setpoint_meaning == "ventilation_start"
    assert s.setpoint_c == pytest.approx(20.0, abs=0.05)
    assert s.design_room_temp_c == pytest.approx(18.5, abs=0.05)
    assert {"code": "heating_below_ventilation_start", "params": {"neutral_zone_c": 1.5}} in s.assumptions


# ─── Klimaatplatform: дни → вес ───────────────────────────────────────────────

def test_nl_finisher_days_mapped_through_tn_select():
    # 23 кг у TN Select (кастраты) — между 20.4 (день 1) и 24.9 (день 8): 1 + 7·2.6/4.5 = 5.044
    # → сдвиг 4.044. День 50 NL = день 54.044 TN: 62.0 + 7.3·4.044/7 = 66.22 кг
    assert G._NL_TN_SHIFT_D == pytest.approx(4.044, abs=0.001)
    finisher = dict(G.KLIMAATPLATFORM_2021.finisher.temp_by_weight)
    assert 66.22 in finisher and finisher[66.22] == 20
    vent = dict(G.KLIMAATPLATFORM_2021.finisher.min_vent_by_weight)
    assert vent[23.0] == 6 and vent[66.22] == 14


def test_nl_room_change_at_23_kg():
    # Конец доращивания 20 °C и начало откорма 25 °C при одном весе — смена помещения
    assert G.setpoints("klimaatplatform_2021", weight_kg=23, phase="nursery").setpoint_c == 20
    assert G.setpoints("klimaatplatform_2021", weight_kg=23, phase="finisher").setpoint_c == 25
    # Без фазы — по границе профиля (23 кг — ещё доращивание)
    s = G.setpoints("klimaatplatform_2021", weight_kg=23)
    assert s.phase == "nursery"
    assert {"code": "phase_from_weight", "params": {"phase": "nursery", "nursery_max_kg": 23.0}} in s.assumptions


def test_nl_nursery_day21_is_flagged_hypothesis():
    # Доля прироста Hypor к 21-му дню: (10.3 + 7.9·6/16 − 6.0)/19.9 = 0.365 → 7.5 + 0.365·15.5 = 13.16
    assert G._NL_NURSERY_KG[21] == pytest.approx(13.16, abs=0.01)
    codes = {a["code"] for a in G.setpoints("klimaatplatform_2021", weight_kg=15).assumptions}
    assert "nursery_day21_weight_from_hypor_shape" in codes


# ─── Hypor ────────────────────────────────────────────────────────────────────

def test_hypor_table7_bands_are_one_curve():
    # Полосы 25–40: 21 → 19; середина 32.5 кг — 20 °C
    assert G.setpoints("hypor_2023", weight_kg=25).setpoint_c == 21
    assert G.setpoints("hypor_2023", weight_kg=32.5).setpoint_c == 20.0
    assert G.setpoints("hypor_2023", weight_kg=105).setpoint_c == 16


def test_hypor_growth_tables_joined_by_weight():
    # 25.9 кг на 42-й день; первая неделя Magnus (30.7 кг) — 42 − 1.105 + 7 = 47.89
    assert G.weight_at_day(G.HYPOR_2023, 42) == 25.9
    assert G.weight_at_day(G.HYPOR_2023, 47.89) == pytest.approx(30.7, abs=0.01)
    assert G.HYPOR_2023.growth[-1][1] == 138.2


# ─── DanBred, РД-АПК, PIC ─────────────────────────────────────────────────────

def test_danbred_room_and_humidity():
    s = G.setpoints("danbred", weight_kg=18)
    assert (s.setpoint_c, s.rh_pct) == (20.0, 70.0)


def test_rd_apk_groups_split_at_40_kg():
    # Табл. 17: отъёмыши 24 °C, откорм 18 °C; RH ≤ 70 %
    assert G.setpoints("rd_apk_2012", weight_kg=30).setpoint_c == 24.0
    assert G.setpoints("rd_apk_2012", weight_kg=45).setpoint_c == 18.0
    assert G.setpoints("rd_apk_2012", weight_kg=45).rh_pct == 70.0


def test_pic_day_to_weight_from_table_41():
    # Табл. 4.1: день 100 — 72 кг, нужная температура 16.0, минимальная вентиляция 12.0
    s = G.setpoints("pic_2019", day=100)
    assert (s.weight_kg, s.setpoint_c, s.min_vent_m3h) == (72.0, 16.0, 12.0)


def test_day_needs_a_growth_curve():
    with pytest.raises(ValueError):
        G.setpoints("rd_apk_2012", day=30)


def test_weight_outside_profile_warns():
    codes = {w["code"] for w in G.setpoints("danbred", weight_kg=60).warnings}
    assert codes == {"weight_outside_profile"}


# ─── Норма из профиля в минимальной вентиляции ────────────────────────────────

FIN = dict(group="growing", weight_kg=120.06, t_inside_c=17.5, t_outside_c=-10, rh_outside_pct=90)


def test_genetics_norm_from_profile():
    r = min_ventilation(**FIN, norm="genetics", genetics="klimaatplatform_2021", phase="finisher")
    assert r.norm_m3h == 17.0
    assert "klimaatplatform_2021" in r.sources


@pytest.mark.parametrize("kw", [dict(genetics="hypor_2023"), dict(genetics=None)])
def test_genetics_norm_needs_a_profile_with_norms(kw):
    with pytest.raises(ValueError):
        min_ventilation(**FIN, norm="genetics", **kw)


def test_genetics_norm_is_for_growing_pigs():
    with pytest.raises(ValueError):
        min_ventilation("dry_sow", 220, 18, -10, 90, norm="genetics", genetics="pic_2019")


# ─── API ──────────────────────────────────────────────────────────────────────

def test_genetics_list(client):
    items = {p["id"]: p for p in client.get("/genetics").json()}
    assert set(items) == set(G.PROFILES)
    assert items["klimaatplatform_2021"]["setpoint_meaning"] == "ventilation_start"
    assert items["hypor_2023"]["has_ventilation_norm"] is False


def test_setpoints_endpoint(client):
    j = client.post("/calc/setpoints", json={"genetics": "pic_2019", "day": 100}).json()
    assert (j["weight_kg"], j["setpoint_c"], j["design_room_temp_c"]) == (72.0, 16.0, 16.0)
    for sid in j["sources"]:
        assert client.get(f"/sources/{sid}").status_code == 200


@pytest.mark.parametrize("body", [{"genetics": "nope", "weight_kg": 50},
                                  {"genetics": "pic_2019"},
                                  {"genetics": "pic_2019", "weight_kg": 50, "day": 60},
                                  {"genetics": "rd_apk_2012", "day": 30}])
def test_setpoints_validation(client, body):
    assert client.post("/calc/setpoints", json=body).status_code == 422


def test_min_ventilation_with_profile_without_norm_is_422(client):
    r = client.post("/calc/min-ventilation", json={**FIN, "norm": "genetics", "genetics": "hypor_2023"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_parameters"
