"""
Этап 4: свиная часть корпусных расчётов и методы API над перенесённой физикой.

Перенос сам по себе проверен паритетом с MINMAXapp (test_house_parity.py); здесь —
эффективная температура свиней, максимальная вентиляция, новый тепловой баланс и
методы /calc/*. Эталоны — руками, арифметика в комментариях.
"""
from __future__ import annotations

import pytest

from backend.app.core import sources
from backend.app.core.max_ventilation import heat_removal_m3h, max_ventilation
from backend.app.core.pig_effective_temp import (effective_temperature, floor_correction_c,
                                                 speed_for_cooling_ms, wind_chill_c)


# ─── Эффективная температура ──────────────────────────────────────────────────

@pytest.mark.parametrize("fpm,chill", [(30, 4.0), (90, 7.0), (300, 10.0)])
def test_mcfarlane_points(fpm, chill):
    assert wind_chill_c(fpm * 0.00508) == pytest.approx(chill)


def test_wind_chill_interpolates_and_caps():
    # 1.0 м/с между 0.457 (−7) и 1.524 (−10): 7 + 3·0.543/1.067 = 8.53
    assert wind_chill_c(1.0) == pytest.approx(8.53, abs=0.01)
    assert wind_chill_c(3.0) == 10.0


@pytest.mark.parametrize("floor,corr", [("concrete_slats", 0.0), ("straw", 7.0), ("asphalt", 5.0),
                                        ("wood", 3.0), ("wet_concrete", -8.5)])
def test_floor_correction_relative_to_slats(floor, corr):
    # Close 1987 табл. 3 относительно дерева: солома −4, асфальт −2, щели +3 → от щелей
    assert floor_correction_c(floor) == corr


def test_speed_needed_to_reach_setpoint():
    # 25 °C на щелях, уставка 21 → нужно 4 °C охлаждения = 30 fpm = 0.152 м/с
    r = effective_temperature(25, 0.0, "concrete_slats", setpoint_c=21)
    assert r.above_setpoint_c == 4.0
    assert r.speed_to_setpoint_ms == pytest.approx(0.15, abs=0.005)
    assert speed_for_cooling_ms(7.0) == pytest.approx(0.457, abs=0.001)


def test_wind_cannot_cool_more_than_ten_degrees():
    r = effective_temperature(30, 0.0, setpoint_c=18)
    assert r.speed_to_setpoint_ms is None
    assert {w["code"] for w in r.warnings} == {"air_speed_cannot_reach_setpoint"}


# ─── Максимальная вентиляция ──────────────────────────────────────────────────

def test_heat_removal_by_hand():
    # Откорм 100 кг, снаружи 30, подъём 3 → внутри 33 °C:
    #   hpu 1000 + 12·(20 − 33) = 844; ур. 32: 0.62·844 − 1.15e-7·33^6 = 523.28 − 148.52 = 374.76
    #   явное 0.37476 · 234.26 = 87.79 Вт; ρ(33) = 353.05/306.15 = 1.15319
    #   Q = 87.79 / (1.15319·1006·3) · 3600 = 90.8 м³/ч на голову
    r = max_ventilation("growing", 100, 1000, 30, norm="none")
    assert r.heat_m3h_per_head == pytest.approx(90.8, abs=0.1)
    assert r.basis == "heat"


def test_walls_take_part_of_the_heat():
    # UA 1500 Вт/К · 3 К = 4.5 кВт уходит через ограждение: (87 790 − 4 500)/1000 голов → 86.2
    r = max_ventilation("growing", 100, 1000, 30, ua_w_per_k=1500, norm="none")
    assert r.heat_m3h_per_head == pytest.approx(86.2, abs=0.1)
    assert heat_removal_m3h(1000, 1000, 25, 3) == 0.0     # ограждение уносит всё — вентиляция не нужна


@pytest.mark.parametrize("group,weight,norm", [("growing", 100, 203.9), ("growing", 15, 68.0),
                                               ("lactating_sow", 220, 1104.4), ("boar", 280, 254.9)])
def test_pic_max_norms(group, weight, norm):
    # PIC Gilt & Sow 2021 табл. 2.4: доращивание 40 cfm = 68.0, Grower 120 cfm = 203.9
    # (до 2026-10-27 откорм брал 150 — это GDU, свинки перед осеменением);
    # опорос летом 650 cfm = 1104.4, хряк 150 cfm = 254.9 м³/ч
    r = max_ventilation(group, weight, 100, 25, norm="pic")
    assert r.norm_m3h_per_head == pytest.approx(norm, abs=0.05)
    assert r.basis == "norm"


@pytest.mark.parametrize("weight,cfm", [(10, 25), (29, 35), (34.0, 35), (34.1, 75), (60, 75),
                                        (90, 120), (130, 120)])
def test_mwps_max_norm_by_weight_class(weight, cfm):
    # PIH-60 табл. 1 «Hot»: 12–30 / 30–75 / 75–150 / 150–220 lb → 25 / 35 / 75 / 120 cfm.
    # Граница 75 lb = 34.02 кг: 34.0 — ещё класс 30–75, 34.1 — уже 75–150.
    # Норма по умолчанию — MWPS (решение владельца 2026-10-27)
    r = max_ventilation("growing", weight, 100, 25)
    assert r.norm_m3h_per_head == pytest.approx(cfm * 1.699011, abs=0.05)
    assert "mwps_pih60" in r.sources
    above = {a["code"] for a in r.assumptions} >= {"mwps_above_table"}
    assert above == (weight > 99.8)


def test_mwps_keeps_pic_for_sows():
    assert max_ventilation("lactating_sow", 220, 10, 25).norm_m3h_per_head == pytest.approx(1104.4, abs=0.05)


def test_pic_norm_implies_about_one_degree_rise():
    # Норма PIC 255 м³/ч соответствует подъёму ≈ 1.2 °C над наружной (решено бисекцией
    # по этой же модели; при меньшем подъёме внутри прохладнее, явная доля больше):
    # PIC держит воздух почти у наружного, а не «+3 °C»
    r = max_ventilation("growing", 100, 1000, 30, norm="none", rise_c=1.22)
    assert r.heat_m3h_per_head == pytest.approx(254.9, rel=0.01)


def test_genetics_max_norm():
    r = max_ventilation("growing", 120.06, 100, 25, norm="genetics", genetics="klimaatplatform_2021",
                        phase="finisher")
    assert r.norm_m3h_per_head == 80.0
    with pytest.raises(ValueError):
        max_ventilation("growing", 60, 100, 25, norm="genetics", genetics="hypor_2023")


def test_hot_inside_warns_rd_apk_limit():
    codes = {w["code"] for w in max_ventilation("growing", 100, 100, 27, norm="none").warnings}
    assert "inside_above_rd_apk_limit" in codes


# ─── Тепловой баланс /calc/heat-balance ───────────────────────────────────────

def test_heat_balance_uses_air_density_inside(client):
    # 12 000 м³/ч, 14.5 °C внутри, −10 снаружи: ρ = 353.05/287.65 = 1.22736;
    # 12000/3600 · 1.22736 · 1006 · 24.5 = 100.84 кВт. Старая константа 0.335 давала 98.49
    j = client.post("/calc/heat-balance", json={"weight_kg": 100, "num_pigs": 1000, "t_outside_c": -10,
                                                "q_min_m3h": 12000, "heater_type": "electric"}).json()
    assert j["q_ventilation_kw"] == pytest.approx(100.84, abs=0.02)


def test_open_flame_needs_its_own_air(client):
    body = {"weight_kg": 30, "num_pigs": 1000, "t_outside_c": -20, "q_min_m3h": 6000,
            "ua_coefficient": 1500, "total_heater_kw": 300}
    gas = client.post("/calc/heat-balance", json={**body, "heater_type": "gas"}).json()
    el = client.post("/calc/heat-balance", json={**body, "heater_type": "electric"}).json()
    assert gas["dilution_air_m3h"] > 0 and el["dilution_air_m3h"] == 0
    assert gas["duty_cycle"] > el["duty_cycle"]          # воздух горелок тоже надо греть
    assert {"code": "gas_heater_treated_as_open_flame"} in gas["heat_assumptions"]


# ─── Методы корпуса ───────────────────────────────────────────────────────────

ENV = {"length_m": 60, "width_m": 12, "wall_height_m": 3,
       "walls": {"u_w_m2k": 0.3}, "roof": {"u_w_m2k": 0.25}}


def test_envelope_by_hand(client):
    # Плоская кровля: бока 2·60·3 = 360, торцы 2·12·3 = 72, кровля 720 м²
    # UA = 0.3·432 + 0.25·720 = 309.6 Вт/К; средний U = 309.6/1152 = 0.269
    j = client.post("/calc/envelope", json=ENV).json()
    assert j["ua_w_per_k"] == 309.6
    assert j["mean_u_w_m2k"] == pytest.approx(0.269, abs=0.001)


@pytest.mark.parametrize("patch,code", [({"walls": {"u_w_m2k": 9}, "roof": {"u_w_m2k": 9}}, "envelope_implausible"),
                                        ({"walls": {"layers": [{"thickness_mm": 100, "material_id": "nope"}]}},
                                         "material_not_found")])
def test_envelope_errors(client, patch, code):
    r = client.post("/calc/envelope", json={**ENV, **patch})
    assert r.status_code == 422 and r.json()["detail"]["code"] == code


def test_materials_list(client):
    ids = {m["id"] for m in client.get("/construction-materials").json()}
    assert {"steel", "pir_core", "brick_exposed"} <= ids


def test_tunnel_fans_matches_the_ported_selection(client):
    # Те же входы, что в паритетной пробе: оригинал MINMAXapp дал 17 вентиляторов при 50.3 Па
    j = client.post("/calc/tunnel-fans", json={
        "target_m3h": 500000, "section_m2": 18 * 3.35,
        "fan_curve": [[0, 40000], [25, 36000], [50, 30000], [75, 22000]]}).json()
    assert j["fans"] == 17 and j["operating_pressure_pa"] == pytest.approx(50.3, abs=0.05)
    assert {w["code"] for w in j["warnings"]} == {"tunnel_length_loss_not_included", "tunnel_pressure_high"}


def test_evap_cooling_endpoint(client):
    body = {"house_length_m": 96, "t_outdoor_db_c": 34, "rh_outdoor_pct": 40, "total_fan_m3h": 300000,
            "pad_length_m": 30, "pad_height_m": 1.8, "t_inside_target_c": 26}
    j = client.post("/calc/evap-cooling", json=body).json()
    assert j["t_pad_out_c"] < 34 and j["water_flow_lpm"] > 0
    # Свиньи греют воздух по длине: у вентиляторов теплее, чем за панелью
    k = client.post("/calc/evap-cooling", json={**body, "num_animals": 1000, "animal_weight_kg": 100}).json()
    assert k["t_fan_end_c"] > k["t_pad_out_c"]
    assert client.post("/calc/evap-cooling", json={**body, "pad_thickness_mm": 50}).status_code == 422


def test_max_ventilation_endpoint(client):
    j = client.post("/calc/max-ventilation", json={"group": "growing", "weight_kg": 100,
                                                   "num_animals": 1000, "t_outside_c": 30}).json()
    # По умолчанию MWPS: 100 кг — верхний класс 120 cfm = 203.9 м³/ч, масса чуть выше таблицы
    assert j["per_head_m3h"] == pytest.approx(203.9, abs=0.05) and j["basis"] == "norm"
    assert {"code": "design_temperature_rise", "params": {"rise_c": 3.0}} in j["assumptions"]


def test_effective_temperature_endpoint(client):
    j = client.post("/calc/effective-temperature", json={"t_air_c": 25, "air_speed_ms": 0.457,
                                                         "setpoint_c": 18}).json()
    assert j["t_effective_c"] == 18.0


def test_house_sources_are_in_catalog(client):
    for sid in ("br443_2019_s3", "br443_2019_s4_3", "sanhe_pads", "uga_pad_velocity", "amca_210",
                "ashrae_2017_ch1", "era5_open_meteo", "mcfarlane_2000", "close_1987", "marcegaglia_dop_100p_2022"):
        assert sources.get_source(sid) is not None, sid
        assert client.get(f"/sources/{sid}").status_code == 200
