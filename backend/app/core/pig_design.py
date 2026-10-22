"""
Сценарий «проект» (API v1 /ventilation-designs) — цикл растущих свиней в корпусе:
по дням кривой роста профиля генетики — уставки, минимальная вентиляция и
отопление при расчётной зиме, летняя вентиляция при расчётном лете; затем
подбор оборудования под худший день.

Аналог ventilation_design.py MINMAXapp. Корпус — геометрия и ограждение
(construction), климат — расчётные температуры (ERA5 через /climate-designs или
свои), оборудование — по перенесённой физике (tunnel_design, evap_cooling_calc).

Поголовье по дням убывает линейно на падёж цикла — допущение (в MINMAXapp своя
модель выживаемости бройлеров, у свиней её нет); первый день — всё поголовье.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import construction as C
from . import evap_cooling_calc as E
from . import genetics as genetics_mod
from . import tunnel_design as T
from .building_thermal import calculate_heat_balance
from .house_checks import Airtightness, airtightness_q4, coldest_surface, condensation
from .max_ventilation import DEFAULT_RISE_C, max_ventilation
from .min_ventilation import (DEFAULT_CO2_LIMIT_PPM, DEFAULT_CO2_OUTDOOR_PPM, DEFAULT_RH_MAX_PCT,
                              min_ventilation)
from .pig_effective_temp import speed_for_cooling_ms
from .pig_heat import DEFAULT_FEED_LEVEL
from .psychro import pressure_at_altitude_pa

# Зимняя наружная RH по умолчанию для расчётного дня: ERA5 /climate-designs её не
# отдаёт (только летнюю совпадающую); 85 % — типичная влажность зимнего воздуха
# умеренного климата (HYPOTHESIS), задаётся параметром
DEFAULT_WINTER_RH_PCT = 85.0
DEFAULT_STEP_DAYS = 7

# Летняя цель — верхняя граница комфорта, а не зимняя уставка: уставка PIC 14 °C
# для 147 кг — точка, выше которой контроллер наращивает вентиляцию, летом её не
# держит ни один свинарник. РД-АПК прим. 3 к табл. 17: внутри «не более чем до
# 26–28 °C» — берём нижний край
DEFAULT_SUMMER_TARGET_C = 26.0

# РД-АПК табл. 18, допустимая скорость воздуха летом, м/с: откорм 1.0; отъёмыши 0.6
RD_SUMMER_SPEED_MAX_MS = {"finisher": 1.0, "nursery": 0.6}


@dataclass
class DesignInput:
    genetics: str
    count: int
    start_day: float
    end_day: float
    length_m: float
    width_m: float
    wall_height_m: float
    winter_design_c: float
    summer_design_db_c: float
    summer_design_rh_pct: float
    ridge_height_m: Optional[float] = None
    ua_w_per_k: Optional[float] = None
    walls: Optional[C.Surface] = None
    roof: Optional[C.Surface] = None
    end_walls: Optional[C.Surface] = None
    winter_rh_pct: float = DEFAULT_WINTER_RH_PCT
    mortality_pct: float = 0.0
    step_days: int = DEFAULT_STEP_DAYS
    phase: Optional[str] = None
    method: str = "cigr2002"
    feed_level: str = DEFAULT_FEED_LEVEL
    floor_feed: str = "dry_feed_dry_floor"
    rh_max_pct: Optional[float] = None
    co2_limit_ppm: float = DEFAULT_CO2_LIMIT_PPM
    co2_outdoor_ppm: float = DEFAULT_CO2_OUTDOOR_PPM
    norm: str = "pic"
    max_norm: str = "pic"
    rise_c: float = DEFAULT_RISE_C
    altitude_m: float = 0.0
    heater_type: str = "gas_open"
    pads: Optional[T.Pads] = None
    fan_curve: Optional[tuple[tuple[float, float], ...]] = None
    summer_target_c: float = DEFAULT_SUMMER_TARGET_C
    airtightness: Optional[Airtightness] = None


@dataclass
class Design:
    days: list[dict]
    equipment: dict
    envelope: dict
    sources: list[str]
    assumptions: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


def _note(code: str, **params) -> dict:
    return {"code": code, "params": params} if params else {"code": code}


def _day_grid(start: float, end: float, step: int) -> list[float]:
    days, d = [], start
    while d < end:
        days.append(d)
        d += step
    days.append(end)
    return days


def design(inp: DesignInput) -> Design:
    profile = genetics_mod.PROFILES[inp.genetics]
    if profile.growth is None:
        raise ValueError(f"profile {inp.genetics} has no growth curve — the design runs by days")
    if inp.end_day <= inp.start_day:
        raise ValueError("end_day must be after start_day")
    assumptions: list[dict] = []
    warnings: list[dict] = []
    sources: list[str] = [profile.source_id]
    p_pa = pressure_at_altitude_pa(inp.altitude_m)

    # ── Ограждение ────────────────────────────────────────────────────────────
    g = C.HouseGeometry(inp.length_m, inp.width_m, inp.wall_height_m, inp.ridge_height_m)
    env_obj = None
    if inp.ua_w_per_k is not None:
        ua, envelope = inp.ua_w_per_k, {"ua_w_per_k": inp.ua_w_per_k, "basis": "given"}
    else:
        e = env_obj = C.envelope_ua(g, inp.walls, inp.roof, inp.end_walls)
        ua = e.ua_w_per_k
        envelope = {"ua_w_per_k": round(ua, 1), "basis": "construction", "mean_u_w_m2k": round(e.mean_u_w_m2k, 3),
                    "areas_m2": {"side_walls": round(e.areas.side_walls_m2, 1),
                                 "end_walls": round(e.areas.end_walls_m2, 1), "roof": round(e.areas.roof_m2, 1)}}
        sources += ["br443_2019_s3", "br443_2019_s4_3"]

    # Утечка при 4 Па — нижний предел воздуха, который греем; без данных — класс moderate
    q4 = airtightness_q4(inp.length_m * inp.width_m, inp.airtightness, assumptions)
    sources.append("uga_leakage")
    surface = coldest_surface(env_obj, ua, g)
    leak_days, cond_days, worst_cond = [], [], None

    mean_h = ((inp.ridge_height_m or inp.wall_height_m) + inp.wall_height_m) / 2.0
    section_m2 = inp.width_m * mean_h
    if inp.mortality_pct > 0:
        assumptions.append(_note("mortality_linear", mortality_pct=inp.mortality_pct))

    rows: list[dict] = []
    span = inp.end_day - inp.start_day
    for day in _day_grid(inp.start_day, inp.end_day, inp.step_days):
        n = max(1, round(inp.count * (1.0 - inp.mortality_pct / 100.0 * (day - inp.start_day) / span)))
        sp = genetics_mod.setpoints(inp.genetics, day=day, phase=inp.phase)
        w, t_in = sp.weight_kg, sp.design_room_temp_c
        rh_max = inp.rh_max_pct or sp.rh_pct or DEFAULT_RH_MAX_PCT

        mv = min_ventilation("growing", w, t_in, inp.winter_design_c, inp.winter_rh_pct,
                             rh_max_pct=rh_max, co2_limit_ppm=inp.co2_limit_ppm,
                             co2_outdoor_ppm=inp.co2_outdoor_ppm, norm=inp.norm, altitude_m=inp.altitude_m,
                             method=inp.method, feed_level=inp.feed_level, floor_feed=inp.floor_feed,
                             genetics=inp.genetics, phase=sp.phase)
        q_min = mv.per_head_m3h * n
        hb = calculate_heat_balance(
            ua_coefficient=ua, q_min_m3h=q_min, t_inside_target=t_in, t_outside=inp.winter_design_c,
            num_animals=n, animal_weight_kg=w, heater_power_kw=0.0, heater_type=inp.heater_type, p_pa=p_pa,
            animals_sensible_w=mv.heat.sensible_w * n,
            co2_limit_ppm=inp.co2_limit_ppm, co2_outdoor_ppm=inp.co2_outdoor_ppm, exchange_floor_m3h=q4)
        if q4 > q_min:
            leak_days.append(day)
        cond = condensation(surface, t_in, rh_max, inp.winter_design_c)
        if cond is not None:
            cond_days.append(day)
            if worst_cond is None or cond[1] - cond[0] > worst_cond[1] - worst_cond[0]:
                worst_cond = cond
        mx = max_ventilation("growing", w, n, inp.summer_design_db_c, rise_c=inp.rise_c, ua_w_per_k=ua,
                             norm=inp.max_norm, genetics=inp.genetics, phase=sp.phase, p_pa=p_pa,
                             method=inp.method, feed_level=inp.feed_level, floor_feed=inp.floor_feed)

        # Скорость в сечении при летнем расходе и охлаждение ею (McFarlane)
        v = mx.total_m3h / 3600.0 / section_m2
        rows.append({
            "day": day, "weight_kg": round(w, 1), "pigs": n, "phase": sp.phase,
            "setpoint_c": sp.setpoint_c, "design_room_temp_c": t_in,
            "min_ventilation_m3h": round(q_min, 0), "min_per_head_m3h": mv.per_head_m3h, "min_basis": mv.basis,
            "humidity_uncontrollable": mv.humidity_uncontrollable,
            "heating_kw": hb.q_heater_required_kw,
            "heating_air_basis": "leakage" if q4 > q_min else "min_ventilation",
            "summer_ventilation_m3h": mx.total_m3h, "summer_basis": mx.basis,
            "summer_section_speed_ms": round(v, 2),
        })
        for src in mv.sources + mx.sources:
            sources.append(src)
        for a in sp.assumptions + mv.assumptions + mx.assumptions:
            if a not in assumptions and a.get("code") not in ("weight_from_growth_curve", "feed_level"):
                assumptions.append(a)
        for wn in sp.warnings + mv.warnings + mx.warnings:
            if wn not in warnings:
                warnings.append(wn)

    if leak_days:
        warnings.append(_note("leakage_exceeds_min_vent", days=leak_days, leak_4pa_m3h=round(q4)))
    if cond_days:
        warnings.append(_note("condensation_risk", element=surface[0], days=cond_days,
                              surface_c=round(worst_cond[0], 1), dew_point_c=round(worst_cond[1], 1)))
    envelope["leak_4pa_m3h"] = round(q4)

    # ── Оборудование под худший день ──────────────────────────────────────────
    min_fans = max(r["min_ventilation_m3h"] for r in rows)
    summer = max(r["summer_ventilation_m3h"] for r in rows)
    heating = max(r["heating_kw"] for r in rows)
    equipment: dict = {
        "min_ventilation_capacity_m3h": min_fans,
        "max_ventilation_capacity_m3h": summer,
        "heating_capacity_kw": heating,
        "worst_heating_day": max(rows, key=lambda r: r["heating_kw"])["day"],
        "worst_summer_day": max(rows, key=lambda r: r["summer_ventilation_m3h"])["day"],
        "section_m2": round(section_m2, 2),
        "section_speed_at_max_ms": round(summer / 3600.0 / section_m2, 2),
    }
    if heating > 0 and inp.heater_type == "gas_open":
        # Мощность — без воздуха разбавления CO₂ самих горелок: с ним (подбор с
        # загрузкой) — сценарий «сейчас» с заданной мощностью
        assumptions.append(_note("heating_without_heater_dilution_air"))

    if inp.pads is not None:
        area = T.pad_area_m2(summer, inp.pads)
        last = rows[-1]
        cool = E.calculate_evap_cooling(E.EvapCoolingInput(
            house_length_m=inp.length_m, t_outdoor_db_c=inp.summer_design_db_c,
            rh_outdoor_pct=inp.summer_design_rh_pct, total_fan_m3h=summer, pad_type=inp.pads.pad_type,
            pad_thickness_mm=inp.pads.thickness_mm, pad_length_m=area / 1.0, pad_height_m=1.0,
            t_inside_target_c=inp.summer_target_c, num_animals=last["pigs"], animal_weight_kg=last["weight_kg"],
            p_pa=p_pa))
        equipment["pads"] = {"pad_type": inp.pads.pad_type, "thickness_mm": inp.pads.thickness_mm,
                             "area_m2": round(area, 1), "t_after_pad_c": cool.t_pad_out_c,
                             "t_at_fans_c": cool.t_fan_end_c, "water_l_h": cool.water_per_hour_l,
                             "status_code": cool.status_code}
        sources += ["sanhe_pads", "uga_pad_velocity"]
        # «Внутри выше 28 °C» выдаёт расчёт летней вентиляции без панелей; если панели
        # держат воздух у вентиляторов в пределе — предупреждение ложное
        if cool.t_fan_end_c <= 28.0:
            warnings = [w for w in warnings if w.get("code") != "inside_above_rd_apk_limit"]

    if inp.fan_curve is not None:
        pad_area = T.pad_area_m2(summer, inp.pads) if inp.pads else None
        res = T.Resistance(section_m2, inp.pads, pad_area)
        sel = T.select_fans(inp.fan_curve, res, summer)
        equipment["tunnel_fans"] = {"count": sel.count, "flow_per_fan_m3h": round(sel.flow_per_fan_m3h, 0),
                                    "operating_pressure_pa": round(sel.operating_pressure_pa, 1),
                                    "extrapolated": sel.extrapolated}
        warnings.append(_note("tunnel_length_loss_not_included"))
        if sel.operating_pressure_pa > T.HIGH_TUNNEL_PRESSURE_PA:
            warnings.append(_note("tunnel_pressure_high", pa=round(sel.operating_pressure_pa, 1)))
        sources.append("amca_210")

    # Летнее ощущение: воздух у вентиляторов (за панелями) или наружный с подъёмом —
    # и скорость, которая сводит эффективную температуру свиней к летней цели (McFarlane)
    if inp.summer_target_c == DEFAULT_SUMMER_TARGET_C:
        assumptions.append(_note("summer_target_rd_apk", t_c=DEFAULT_SUMMER_TARGET_C))
    t_air = equipment["pads"]["t_at_fans_c"] if "pads" in equipment else inp.summer_design_db_c + inp.rise_c
    need = t_air - inp.summer_target_c
    speed = speed_for_cooling_ms(need)
    equipment["summer_air_c"] = round(t_air, 1)
    equipment["speed_to_summer_target_ms"] = round(speed, 2) if speed is not None else None
    if speed is None:
        warnings.append(_note("air_speed_cannot_reach_target", cooling_needed_c=round(need, 1), max_cooling_c=10.0))
    else:
        phase = rows[-1]["phase"]
        limit = RD_SUMMER_SPEED_MAX_MS[phase]
        if speed > limit:
            warnings.append(_note("air_speed_above_rd_apk", speed_ms=round(speed, 2), limit_ms=limit))
    sources += ["mcfarlane_2000", "rd_apk_2012"]

    return Design(days=rows, equipment=equipment, envelope=envelope,
                  sources=list(dict.fromkeys(sources)), assumptions=assumptions, warnings=warnings)
