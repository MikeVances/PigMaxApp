"""
Пробы паритета физики корпуса с оригиналом MINMAXapp (faithful-porting, проход 3).

Те же входы прогонялись через оригинал MINMAXapp 59390c7 — его результаты в
backend/tests/data/minmax_house_parity_59390c7.json. Здесь их считает порт.
Биология исключена: поголовье 0, тепло животных передаётся числом.
"""
import dataclasses

from backend.app.core import building_thermal as B
from backend.app.core import construction as C
from backend.app.core import design_climate as D
from backend.app.core import evap_cooling_calc as E
from backend.app.core import psychro as P
from backend.app.core import tunnel_design as T


def run() -> dict:
    out = {}
    g = C.HouseGeometry(length_m=96, width_m=18, wall_height_m=2.6, ridge_height_m=4.1)
    walls = C.Surface(layers=(C.Layer(0.5, material_id="steel"), C.Layer(100, lambda_w_mk=0.022), C.Layer(0.5, material_id="steel")))
    roof = C.Surface(u_w_m2k=0.25)
    ends = C.Surface(layers=(C.Layer(250, material_id="brick_exposed"), C.Layer(20, material_id="mortar_protected")))
    ua = C.envelope_ua(g, walls, roof, ends)
    out["envelope"] = [ua.ua_w_per_k, ua.side_walls_u, ua.end_walls_u, ua.roof_u, ua.areas.total_m2, ua.areas.roof_pitch_deg]
    for bad in (C.Layer(0, material_id="steel"), C.Layer(10), C.Layer(10, material_id="nope")):
        try: C.layer_resistance(bad); out.setdefault("layer_err", []).append(None)
        except Exception as e: out.setdefault("layer_err", []).append([type(e).__name__, getattr(e, "code", None)])
    n = 24 * 366
    temps = [-25 + 60 * ((i * 7919) % n) / n for i in range(n)]
    rhs = [30 + 60 * ((i * 104729) % n) / n for i in range(n)]
    dc = D.design_conditions(temps, rhs, 250.0)
    out["design"] = dataclasses.asdict(dc)
    for pad in ("7090", "7060", "5090"):
        for th, q25 in ((150, 0), (100, 0.85)):
            if th not in E.SANHE_PADS[pad]["pressure_pa"]: continue
            inp = E.EvapCoolingInput(house_length_m=96, t_outdoor_db_c=34, rh_outdoor_pct=40, total_fan_m3h=300000,
                                     pad_type=pad, pad_thickness_mm=th, pad_length_m=30, pad_height_m=1.8,
                                     t_inside_target_c=26, total_fan_25pa_m3h=300000 * q25, water_supply_t_c=12.0)
            r = dataclasses.asdict(E.calculate_evap_cooling(inp))
            out[f"evap_{pad}_{th}"] = r
    pts = ((0, 40000), (25, 36000), (50, 30000), (75, 22000))
    res = T.Resistance(section_m2=18 * 3.35, pads=T.Pads("7090", 150), pad_area=T.pad_area_m2(500000, T.Pads()))
    sel = T.select_fans(pts, res, 500000)
    out["fans"] = [sel.count, sel.flow_per_fan_m3h, sel.operating_pressure_pa, sel.extrapolated, dataclasses.asdict(res.parts(500000))]
    out["fanflow"] = [T.fan_flow_m3h(pts, p) for p in (0, 10, 60, 120)]
    out["co2dil"] = [B.heater_co2_dilution_m3h(200, d, "gas_open") for d in (0, 0.3, 1)] + [B.heater_co2_dilution_m3h(200, 1, "electric")]
    out["air"] = [B.heater_air_demand(200, 0.5, "gas_open"), B.ventilation_heat_w(20000, 18, -10), B.air_density_kg_m3(-10)]
    sol = B.solve_heater_duty(lambda d: 20000 + B.heater_co2_dilution_m3h(250, d, "gas_open"), 1500, 18, -15, 60000, 250000)
    out["duty"] = dataclasses.asdict(sol)
    sol2 = B.solve_heater_duty(lambda d: 20000, 1500, 18, -30, 10000, 100000, exchange_floor_m3h=25000)
    out["duty2"] = dataclasses.asdict(sol2)
    bd = B.BuildingDimensions(96, 18, 2.6)
    out["cond"] = list(B.surface_condensation(4000, bd, 18, 70, -15))
    out["psy"] = [P.wet_bulb_c(30, 50), P.wet_bulb_c(-5, 80), P.dew_point_c(20, 60), P.moist_air_density(25, 60)]
    for name, args in {
        "partial_gas": (lambda d: 15000 + B.heater_co2_dilution_m3h(400, d, "gas_open"), 800, 18, -5, 90000, 400000),
        "partial_el":  (lambda d: 15000, 800, 18, -5, 90000, 400000),
        "no_heater":   (lambda d: 15000, 800, 18, -5, 90000, 0),
        "self_heated": (lambda d: 3000, 300, 18, 5, 200000, 300000),
        "floor":       (lambda d: 5000, 800, 18, -5, 20000, 400000, 12000),
    }.items():
        out["duty_" + name] = dataclasses.asdict(B.solve_heater_duty(*args))
    return out
