"""
Этап 1: тепло, пар и CO₂ свиней всех групп (core/pig_heat.py).

Эталоны посчитаны руками по формулам первоисточников (docs/sources.md), а не
взяты из прогона кода: арифметика расписана в комментариях, её можно проверить
калькулятором по тексту в refs/.
"""
from __future__ import annotations

import pytest

from backend.app.core import pig_heat as P
from backend.app.core import sources
from backend.app.core.pig_heat import MethodNotApplicable, pig_heat


# ─── CIGR 2002 ────────────────────────────────────────────────────────────────

def test_cigr_finisher_is_maintenance_plus_feed():
    # Ур. 8, 100 кг, DK 900 г/сут (n = 2.98), 20 °C:
    #   поддержание 5.09·100^0.75 = 5.09·31.623 = 160.96 Вт
    #   + (1 − 0.77)·(2.98 − 1)·160.96 = 0.23·1.98·160.96 = 73.30 Вт → 234.26 Вт
    h = pig_heat("growing", 100, 20)
    assert h.total_w == pytest.approx(234.3, abs=0.1)
    # Старая модель PigMaxApp давала только поддержание — 161 Вт
    assert h.total_w > 161 * 1.4


def test_cigr_sensible_is_house_level_eq32():
    # Ур. 32 при 20 °C: 0.62·1000 − 1.15e-7·20^6 = 620 − 7.36 = 612.64 на hpu
    #   → 234.26 · 0.61264 = 143.5 Вт; скрытое 90.7 Вт; пар 90.7·3600/2448 = 133.4 г/ч
    h = pig_heat("growing", 100, 20)
    assert h.sensible_w == pytest.approx(143.5, abs=0.1)
    assert h.latent_w == pytest.approx(h.total_w - h.sensible_w, abs=0.15)
    assert h.vapour_g_h == pytest.approx(133.4, abs=0.2)


def test_cigr_total_heat_falls_1_2_pct_per_degree():
    # Ур. 25: при 10 °C — ×(1000 + 12·10)/1000 = 1.12; при 30 °C — ×0.88
    assert pig_heat("growing", 100, 10).total_w == pytest.approx(234.26 * 1.12, abs=0.15)
    assert pig_heat("growing", 100, 30).total_w == pytest.approx(234.26 * 0.88, abs=0.15)


def test_cigr_co2_is_on_total_heat_at_20c():
    # 0.185 м³/ч на 1000 Вт при 20 °C: 0.185 · 234.26 = 43.3 л/ч — и при 10 °C
    # столько же: база CO₂ — тепло при 20 °C, не при температуре помещения
    assert pig_heat("growing", 100, 20).co2_l_h == pytest.approx(43.3, abs=0.1)
    assert pig_heat("growing", 100, 10).co2_l_h == pytest.approx(43.3, abs=0.1)


def test_cigr_piglet_eq6_and_2pct_coefficient():
    # 8 кг, 28 °C: n = 4.1 + (3.39 − 4.1)·6/18 = 3.863
    #   поддержание 7.4·8^0.66 = 7.4·3.945 = 29.19; KY = 0.494
    #   при 20 °C: 29.19 + 0.506·2.863·29.19 = 71.48 Вт
    #   коэффициент 20: (1000 − 20·8)/1000 = 0.84 → 60.05 Вт
    h = pig_heat("growing", 8, 28)
    assert h.total_w == pytest.approx(60.0, abs=0.1)
    assert {"code": "piglet_temperature_coefficient", "params": {"pct_per_c": 2.0}} in h.assumptions


def test_feed_level_is_a_parameter():
    # Шведская норма, 100 кг: n = 2.45 → 160.96 + 0.23·1.45·160.96 = 214.6 Вт
    h = pig_heat("growing", 100, 20, feed_level="S")
    assert h.total_w == pytest.approx(214.6, abs=0.1)
    assert {"code": "feed_level", "params": {"feed_level": "S", "n": 2.45}} in h.assumptions


def test_heavy_finisher_warns_feed_level_extrapolated():
    codes = {w["code"] for w in pig_heat("growing", 135, 18).warnings}
    assert "feed_level_extrapolated" in codes
    assert pig_heat("growing", 100, 18).warnings == []


def test_cigr_lactating_sow_and_milk_yield():
    # Ур. 10, 220 кг, 20 °C: 4.85·220^0.75 + 28·6 = 4.85·57.11 + 168 = 445.0 Вт
    assert pig_heat("lactating_sow", 220, 20).total_w == pytest.approx(445.0, abs=0.3)
    # 11 кг молока — +28·5 = 140 Вт: молочность — параметр, значение CIGR устарело
    assert pig_heat("lactating_sow", 220, 20, milk_kg_day=11).total_w == pytest.approx(585.0, abs=0.3)


def test_cigr_gestation_day_adds_heat():
    # Ур. 9: 8e-5·110³ = 106.5 Вт к 110-му дню против холостой
    gest = pig_heat("gestating_sow", 220, 20, pregnancy_day=110).total_w
    dry = pig_heat("dry_sow", 220, 20).total_w
    assert gest - dry == pytest.approx(106.5, abs=0.2)


def test_gestating_sow_needs_pregnancy_day():
    with pytest.raises(ValueError):
        pig_heat("gestating_sow", 220, 20)


def test_sows_use_general_sensible_eq30():
    # Ур. 30 при 20 °C: 0.8·1000 − 0.38·400 = 648 на hpu (а не 612.6 по ур. 32)
    h = pig_heat("dry_sow", 200, 20)
    assert h.sensible_w / h.total_w == pytest.approx(0.648, abs=0.001)


# ─── Brown-Brandl 2004 ────────────────────────────────────────────────────────

def test_brown_brandl_eq13():
    # 100 кг, 20 °C: log10(HP) = 1.189 − 0.1 − 0.345·2 = 0.399 → 2.506 Вт/кг → 250.6 Вт
    h = pig_heat("growing", 100, 20, method="brown_brandl_2004")
    assert h.total_w == pytest.approx(250.6, abs=0.1)
    assert h.source_id == "brown_brandl_2004"


def test_brown_brandl_is_for_growing_pigs_only():
    with pytest.raises(MethodNotApplicable):
        pig_heat("lactating_sow", 220, 20, method="brown_brandl_2004")


def test_brown_brandl_warns_outside_data_range():
    codes = {w["code"] for w in pig_heat("growing", 120, 18, method="brown_brandl_2004").warnings}
    assert codes == {"weight_outside_source_range"}


# ─── РД-АПК 1.10.02.04-12 ─────────────────────────────────────────────────────

def test_rd_apk_row_at_10c():
    # Табл. 15, откорм 100 кг: 1204.2 / 866.7 кДж/ч, 138 г/ч; CO₂ 0.036·1204.2 = 43.35 л/ч
    h = pig_heat("growing", 100, 10, method="rd_apk_2012")
    assert h.total_w == pytest.approx(1204.2 / 3.6, abs=0.1)
    assert h.sensible_w == pytest.approx(866.7 / 3.6, abs=0.1)
    assert h.vapour_g_h == 138.0
    assert h.co2_l_h == pytest.approx(43.4, abs=0.05)


def test_rd_apk_temperature_coefficients():
    # Табл. 16 при 20 °C: ×0.90 / ×0.67 / ×1.50
    h = pig_heat("growing", 100, 20, method="rd_apk_2012")
    assert h.total_w == pytest.approx(1204.2 * 0.90 / 3.6, abs=0.1)
    assert h.sensible_w == pytest.approx(866.7 * 0.67 / 3.6, abs=0.1)
    assert h.vapour_g_h == pytest.approx(138 * 1.5, abs=0.05)
    # CO₂ табл. 16 не пересчитывает
    assert h.co2_l_h == pytest.approx(43.4, abs=0.05)


def test_rd_apk_interpolates_mass_and_temperature():
    # 95 кг, 12.5 °C: (1133.3 + 1204.2)/2 = 1168.75 кДж/ч × (1.00 + 0.94)/2 = 0.97 → 314.9 Вт
    assert pig_heat("growing", 95, 12.5, method="rd_apk_2012").total_w == pytest.approx(314.9, abs=0.1)


def test_rd_apk_late_gestation_row():
    # С 105-го дня — строка «тяжелосупоросные»: 200 кг 1595.8 против 1345.8 кДж/ч
    late = pig_heat("gestating_sow", 200, 10, method="rd_apk_2012", pregnancy_day=105)
    early = pig_heat("gestating_sow", 200, 10, method="rd_apk_2012", pregnancy_day=60)
    assert late.total_w == pytest.approx(1595.8 / 3.6, abs=0.1)
    assert early.total_w == pytest.approx(1345.8 / 3.6, abs=0.1)


def test_rd_apk_boar_typo_fixed_from_kcal():
    # В таблице «2116,7 (292)» — свободная больше общей. 292 ккал/ч · 4.1868 = 1222.5 кДж/ч
    assert P.RD_BOAR[0][2] == pytest.approx(292 * 4.1868, abs=0.1)
    h = pig_heat("boar", 200, 10, method="rd_apk_2012")
    assert h.sensible_w < h.total_w


ALL_RD_ROWS = [(name, row) for name in ("RD_GROWING", "RD_BOAR", "RD_SOW_DRY_GESTATING",
                                        "RD_SOW_LATE_GESTATING", "RD_SOW_LACTATING")
               for row in getattr(P, name)]


@pytest.mark.parametrize("name,row", ALL_RD_ROWS, ids=[f"{n}-{r[0]}" for n, r in ALL_RD_ROWS])
def test_rd_apk_rows_are_self_consistent(name, row):
    # Инварианты самой нормы ловят ошибку переноса любого числа строки:
    #   прим. 2 — свободная теплота при 10 °C = 72 % общей;
    #   пар = (общая − свободная) / 2.45 кДж/г (сходится в пределах 2.2 % по всей таблице)
    mass, total, free, vapour = row
    assert free / total == pytest.approx(0.72, abs=0.006)
    assert (total - free) / 2.45 == pytest.approx(vapour, rel=0.03)


def test_rd_apk_warns_outside_rows():
    codes = {w["code"] for w in pig_heat("lactating_sow", 260, 35, method="rd_apk_2012").warnings}
    assert codes == {"weight_outside_source_range", "temperature_outside_source_range"}


# ─── Источники ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method", ["cigr2002", "brown_brandl_2004", "rd_apk_2012"])
def test_every_method_source_is_in_catalog(method):
    assert sources.get_source(P.METHOD_SOURCE[method]) is not None


# ─── API ──────────────────────────────────────────────────────────────────────

def test_heat_production_endpoint(client):
    r = client.post("/calc/heat-production", json={"group": "growing", "weight_kg": 100, "t_inside_c": 20})
    assert r.status_code == 200
    j = r.json()
    assert j["total_w"] == pytest.approx(234.3, abs=0.1) and j["sources"] == ["cigr_2002"]
    for sid in j["sources"]:
        assert client.get(f"/sources/{sid}").status_code == 200


def test_heat_production_validation(client):
    url = "/calc/heat-production"
    assert client.post(url, json={"group": "gestating_sow", "weight_kg": 220, "t_inside_c": 18}).status_code == 422
    r = client.post(url, json={"group": "boar", "weight_kg": 250, "t_inside_c": 16, "method": "brown_brandl_2004"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "method_not_applicable"
    assert client.get("/sources/nope").status_code == 404


def test_ventilation_reports_heat_at_setpoint(client):
    # 100 кг, зима: уставка 14.5 °C → 234.26·(1000 + 12·5.5)/1000 = 234.26·1.066 = 249.7 Вт
    j = client.post("/calc/ventilation", json={"weight_kg": 100, "num_pigs": 1000, "t_outside_c": -10}).json()
    assert j["pig_heat_w_per_head"] == pytest.approx(249.7, abs=0.1)
    assert j["heat_method"] == "cigr2002"
    assert all(sources.get_source(s) for s in j["sources"])


def test_heat_balance_credits_only_sensible_heat(client):
    # 1000 голов × 100 кг при нужной температуре 14.5 °C:
    #   hpu: 1000 + 12·5.5 = 1066; явное 0.62·1066 − 1.15e-7·14.5^6 = 660.92 − 1.07 = 659.85
    #   → 234.26 · 0.65985 = 154.6 Вт/гол → 154.6 кВт. Старый код засчитывал 161 кВт полного
    #   тепла поддержания, и совпадение порядка было случайным: модель была неверной в обе стороны
    j = client.post("/calc/heat-balance", json={"weight_kg": 100, "num_pigs": 1000,
                                                "t_outside_c": -10, "q_min_m3h": 12000}).json()
    assert j["q_pigs_kw"] == pytest.approx(154.6, abs=0.1)
    assert j["q_pigs_total_kw"] == pytest.approx(249.7, abs=0.1)
    assert j["heat_method"] == "cigr2002"
