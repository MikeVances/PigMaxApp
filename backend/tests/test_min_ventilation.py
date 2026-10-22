"""
Этап 2: минимальная вентиляция = max(норма, влага, CO₂) (core/min_ventilation.py).

Эталоны — руками по формулам источников; арифметика в комментариях.
"""
from __future__ import annotations

import pytest

from backend.app.core import min_ventilation as M
from backend.app.core import psychro
from backend.app.core.min_ventilation import min_ventilation
from backend.app.core.pig_heat import pig_heat

# Откорм 100 кг при зимней уставке PIC 14.5 °C, снаружи −10 °C / 90 %
FIN = dict(group="growing", weight_kg=100, t_inside_c=14.5, t_outside_c=-10, rh_outside_pct=90)


# ─── CO₂ ──────────────────────────────────────────────────────────────────────

def test_co2_criterion_reproduces_pic_table():
    # CO₂ 0.185 · 234.26 = 43.34 л/ч; (43.34/1000) / ((3000 − 420)·1e-6) = 16.80 м³/ч.
    # PIC табл. 4.1 при 98 кг — 16.8: норма PIC и есть CO₂-баланс, два пути сходятся
    r = min_ventilation(**FIN)
    assert r.co2_m3h == pytest.approx(16.8, abs=0.05)
    assert r.co2_m3h == pytest.approx(r.norm_m3h, rel=0.03)


def test_stricter_russian_co2_limit():
    # 2000 ppm (РД-АПК п. 14.4): 0.04334 / (1580e-6) = 27.4 м³/ч
    assert min_ventilation(**FIN, co2_limit_ppm=2000).co2_m3h == pytest.approx(27.4, abs=0.05)


def test_co2_limit_must_exceed_outdoor():
    with pytest.raises(ValueError):
        min_ventilation(**FIN, co2_limit_ppm=400, co2_outdoor_ppm=420)


# ─── Влага ────────────────────────────────────────────────────────────────────

def test_humidity_ratios_by_hand():
    # Магнус: 611.2·exp(256.215/258 = 0.993081) = 1649.97 Па; ×0.65 = 1072.48;
    # W = 0.621945·1072.48 / (101325 − 1072.48) = 667.023 / 100252.52
    assert psychro.humidity_ratio(14.5, 65) == pytest.approx(0.0066534, abs=1e-7)
    # 611.2·exp(−176.7/233.5) = 286.78 Па; ×0.9 = 258.10; W = 0.621945·258.10/101066.9
    assert psychro.humidity_ratio(-10, 90) == pytest.approx(0.0015883, abs=2e-7)


def test_moisture_criterion_by_hand():
    # Скрытое при 14.5 °C: 249.72 − 154.58 = 95.14 Вт → 95.14·3600/2448 = 139.9 г/ч
    # ρ сухого воздуха внутри 1.2142 кг/м³; ΔW = 6.654 − 1.588 = 5.065 г/кг
    # Q = 0.13991 / (1.2142 · 0.0050653) = 22.75 м³/ч — больше нормы PIC 17.1
    r = min_ventilation(**FIN)
    assert r.delta_w_g_per_kg == pytest.approx(5.07, abs=0.01)
    assert r.moisture_m3h == pytest.approx(22.7, abs=0.06)
    assert r.basis == "moisture" and r.per_head_m3h == r.moisture_m3h


def test_mild_wet_weather_needs_much_more_air():
    # +5 °C / 90 %: снаружи почти так же влажно, ΔW 1.8 г/кг — расход ×2.8 против мороза.
    # Ровно случай PIC с. 17 «RH > 65 % и снаружи холоднее уставки — увеличить воздухообмен»
    r = min_ventilation(**{**FIN, "t_outside_c": 5})
    assert r.basis == "moisture"
    assert r.per_head_m3h > 2.5 * min_ventilation(**FIN).per_head_m3h


def test_wetter_outside_air_is_uncontrollable():
    # +12 °C / 90 %: W снаружи 7.84 г/кг > 6.65 внутри — никакой расход не удержит 65 %
    r = min_ventilation(**{**FIN, "t_outside_c": 12})
    assert r.humidity_uncontrollable and r.moisture_m3h is None
    assert {"humidity_uncontrollable"} <= {w["code"] for w in r.warnings}
    assert r.basis in ("co2", "norm")


def test_moisture_flow_does_not_depend_on_altitude():
    # ρ сухого воздуха ∝ p, ΔW ∝ 1/p — объёмный расход по влаге от высоты не зависит.
    # Давление, учтённое только в одной из формул, сдвинуло бы его на 12 % на 1000 м
    low = min_ventilation(**FIN).moisture_m3h
    high = min_ventilation(**FIN, altitude_m=1000).moisture_m3h
    assert high == pytest.approx(low, rel=0.01)


def test_liquid_feed_shifts_sensible_heat_to_vapour():
    # CIGR табл. 3.1: влажный корм и мокрый пол — ks 0.9: 10 % явного уходит в пар
    dry = pig_heat("growing", 100, 14.5)
    wet = pig_heat("growing", 100, 14.5, floor_feed="wet_feed_wet_floor")
    assert wet.sensible_w == pytest.approx(dry.sensible_w * 0.9, abs=0.1)
    assert wet.vapour_g_h == pytest.approx(dry.vapour_g_h + dry.sensible_w * 0.1 * 3600 / 2448, abs=0.1)
    assert wet.total_w == dry.total_w
    assert min_ventilation(**FIN, floor_feed="wet_feed_wet_floor").moisture_m3h > min_ventilation(**FIN).moisture_m3h


def test_higher_rh_limit_needs_less_air():
    # РД-АПК допускает 70 % для откорма — влажностный расход ниже, чем при 65 % PIC
    assert min_ventilation(**FIN, rh_max_pct=70).moisture_m3h < min_ventilation(**FIN).moisture_m3h


# ─── Нормы ────────────────────────────────────────────────────────────────────

def test_pic_sow_norms_from_cfm():
    # PIC Gilt & Sow 2021: 12 cfm × 1.699 = 20.4 м³/ч; опорос 20 cfm = 34.0 м³/ч
    gest = min_ventilation("gestating_sow", 220, 18, -10, 90, pregnancy_day=80)
    lact = min_ventilation("lactating_sow", 220, 22, -10, 90)
    assert gest.norm_m3h == pytest.approx(20.4, abs=0.05)
    assert lact.norm_m3h == pytest.approx(34.0, abs=0.05)
    assert "pic_2021_sow" in gest.sources


def test_boar_norm_is_flagged_as_borrowed():
    r = min_ventilation("boar", 280, 16, -10, 90)
    assert {"code": "boar_norm_from_breeding_area"} in r.assumptions


@pytest.mark.parametrize("t_out,period,expected", [(-1, "cold", 30), (0, "transition", 45),
                                                    (10, "transition", 45), (10.1, "warm", 60)])
def test_rd_apk_norm_by_period(t_out, period, expected):
    # П. 14.6: 30 / 45 / 60 м³/ч на 100 кг; тёплый — выше 10 °C (прим. 3), граница 0 °C — наша
    r = min_ventilation(**{**FIN, "t_outside_c": t_out}, norm="rd_apk_2012")
    assert r.norm_m3h == pytest.approx(expected, abs=0.01)
    assert {"code": "period_from_outside_temperature",
            "params": {"period": period, "warm_above_c": 10.0, "cold_below_c": 0.0}} in r.assumptions


def test_explicit_period_wins():
    r = min_ventilation(**FIN, norm="rd_apk_2012", period="warm")
    assert r.norm_m3h == 60.0
    assert not any(a["code"] == "period_from_outside_temperature" for a in r.assumptions)


def test_rd_apk_lactating_norm_counts_litter():
    # (220 + 80) кг / 100 · 30 = 90 м³/ч; без массы гнезда — 66 и допущение
    with_litter = min_ventilation("lactating_sow", 220, 22, -10, 90, norm="rd_apk_2012", litter_mass_kg=80)
    without = min_ventilation("lactating_sow", 220, 22, -10, 90, norm="rd_apk_2012")
    assert with_litter.norm_m3h == pytest.approx(90.0, abs=0.01)
    assert without.norm_m3h == pytest.approx(66.0, abs=0.01)
    assert {"code": "litter_mass_not_counted"} in without.assumptions


def test_no_norm_is_physics_only():
    r = min_ventilation(**FIN, norm="none")
    assert r.norm_m3h is None and r.per_head_m3h == max(r.co2_m3h, r.moisture_m3h)


# ─── Максимум ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kw", [FIN, {**FIN, "t_outside_c": 12},
                                dict(group="growing", weight_kg=8, t_inside_c=28, t_outside_c=-10, rh_outside_pct=90),
                                dict(group="lactating_sow", weight_kg=220, t_inside_c=22, t_outside_c=-10,
                                     rh_outside_pct=90, milk_kg_day=11)])
def test_result_is_the_largest_criterion(kw):
    r = min_ventilation(**kw)
    values = {"co2": r.co2_m3h, "moisture": r.moisture_m3h, "norm": r.norm_m3h}
    present = {k: v for k, v in values.items() if v is not None}
    assert r.per_head_m3h == max(present.values())
    assert values[r.basis] == r.per_head_m3h


def test_every_cited_source_is_in_catalog():
    from backend.app.core import sources
    for kw in (FIN, dict(group="gestating_sow", weight_kg=220, t_inside_c=18, t_outside_c=-10,
                         rh_outside_pct=90, pregnancy_day=80)):
        for norm in ("pic", "rd_apk_2012", "none"):
            for sid in min_ventilation(**kw, norm=norm).sources:
                assert sources.get_source(sid) is not None, sid


# ─── API ──────────────────────────────────────────────────────────────────────

def test_min_ventilation_endpoint(client):
    r = client.post("/calc/min-ventilation", json={**FIN, "num_animals": 1000})
    assert r.status_code == 200
    j = r.json()
    assert j["per_head_m3h"] == pytest.approx(22.7, abs=0.06)
    assert j["total_m3h"] == pytest.approx(j["per_head_m3h"] * 1000, abs=1)
    assert j["basis"] == "moisture"
    assert set(j["breakdown"]) == {"co2_m3h", "moisture_m3h", "norm_m3h", "delta_w_g_per_kg"}
    for sid in j["sources"]:
        assert client.get(f"/sources/{sid}").status_code == 200


def test_min_ventilation_endpoint_requires_outside_humidity(client):
    body = {k: v for k, v in FIN.items() if k != "rh_outside_pct"}
    assert client.post("/calc/min-ventilation", json=body).status_code == 422


def test_ventilation_uses_the_maximum_not_the_table(client):
    # Раньше /calc/ventilation отдавал табл. PIC — 17.1 м³/ч при 100 кг; влага требует 22.7
    j = client.post("/calc/ventilation", json={"weight_kg": 100, "num_pigs": 1000,
                                               "t_outside_c": -10, "rh_outside_pct": 90}).json()
    assert j["min_vent_per_head_m3h"] == pytest.approx(22.7, abs=0.06)
    assert j["min_vent_total_m3h"] == pytest.approx(22700, abs=60)
    assert j["min_vent_basis"] == "moisture"


def test_ventilation_flags_default_outside_humidity(client):
    j = client.post("/calc/ventilation", json={"weight_kg": 100, "num_pigs": 1000, "t_outside_c": -10}).json()
    assert {"code": "outside_rh_default", "params": {"rh_pct": 80.0}} in j["assumptions"]
