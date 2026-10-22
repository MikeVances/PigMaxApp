"""
Сценарий «потребность сейчас» (API v1 /ventilation-requirements) — одна группа
свиней в текущих условиях: уставки, тепло, минимальная и летняя вентиляция,
отопление и эффективная температура.

Аналог ventilation_requirement.py MINMAXapp, собран из модулей этапов 1–5:
уставки — профиль генетики (растущие) или профиль свиноматок; тепло — pig_heat;
минимум — min_ventilation; лето — max_ventilation; баланс — перенесённый решатель
горелок (building_thermal); ощущение — pig_effective_temp.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import genetics as genetics_mod
from . import sow_climate
from .building_thermal import calculate_heat_balance
from .max_ventilation import DEFAULT_RISE_C, max_ventilation
from .min_ventilation import (DEFAULT_CO2_LIMIT_PPM, DEFAULT_CO2_OUTDOOR_PPM, DEFAULT_RH_MAX_PCT,
                              min_ventilation)
from .pig_effective_temp import effective_temperature
from .pig_heat import DEFAULT_FEED_LEVEL
from .psychro import pressure_at_altitude_pa

SOW_GROUPS = ("dry_sow", "gestating_sow", "lactating_sow")
SOW_STAGE = {"dry_sow": "dry", "gestating_sow": "gestation", "lactating_sow": "lactation"}


@dataclass
class RequirementInput:
    group: str
    count: int
    t_outside_c: float
    rh_outside_pct: float
    weight_kg: Optional[float] = None
    genetics: Optional[str] = None            # растущие: профиль генетики
    day: Optional[float] = None               # растущие: день после отъёма — вес по кривой профиля
    phase: Optional[str] = None
    sow_profile: Optional[str] = None         # свиноматки: профиль климата
    pregnancy_day: Optional[float] = None
    lactation_day: Optional[float] = None
    litter_mass_kg: Optional[float] = None
    milk_kg_day: Optional[float] = None
    covered_nest: bool = False
    t_inside_c: Optional[float] = None        # явно — вместо расчётной уставки профиля
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
    ua_w_per_k: Optional[float] = None
    heater_power_kw: float = 0.0
    heater_type: str = "gas_open"
    creep_heat_w_per_pen: float = 0.0
    floor: str = "concrete_slats"
    air_speed_ms: Optional[float] = None


@dataclass
class Requirement:
    weight_kg: float
    t_inside_c: float
    setpoints: dict
    heat: dict
    min_ventilation: dict
    max_ventilation: dict
    heating: Optional[dict]
    effective_temperature: Optional[dict]
    sources: list[str]
    assumptions: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


def _note(code: str, **params) -> dict:
    return {"code": code, "params": params} if params else {"code": code}


def requirement(inp: RequirementInput) -> Requirement:
    assumptions: list[dict] = []
    warnings: list[dict] = []
    sources: list[str] = []
    heat_kw = dict(pregnancy_day=inp.pregnancy_day, milk_kg_day=inp.milk_kg_day,
                   lactation_day=inp.lactation_day, litter_mass_kg=inp.litter_mass_kg)

    # ── Уставки ───────────────────────────────────────────────────────────────
    if inp.group in SOW_GROUPS:
        if inp.weight_kg is None:
            raise ValueError("weight_kg is required for sows")
        weight = inp.weight_kg
        profile = inp.sow_profile or "pic_2021"
        if inp.sow_profile is None:
            assumptions.append(_note("sow_profile_default", profile=profile))
        lday = inp.lactation_day if inp.group == "lactating_sow" else None
        sp = sow_climate.sow_setpoints(profile, SOW_STAGE[inp.group], lday, inp.covered_nest)
        setpoints = {"profile": sp.profile_id, "setpoint_c": sp.room_setpoint_c,
                     "setpoint_meaning": sp.setpoint_meaning, "design_room_temp_c": sp.design_room_temp_c,
                     "nest_c": sp.nest_c, "rh_pct": sp.rh_pct,
                     "norm_min_m3h": sp.min_vent_m3h, "norm_max_m3h": sp.max_vent_m3h}
        design_t, profile_rh = sp.design_room_temp_c, sp.rh_pct
        sources.append(sp.source_id)
        assumptions += sp.assumptions
        warnings += sp.warnings
    elif inp.group == "growing":
        if inp.genetics is None:
            if inp.weight_kg is None:
                raise ValueError("weight_kg or genetics with day is required")
            profile = "pic_2019"
            assumptions.append(_note("genetics_default", profile=profile))
        else:
            profile = inp.genetics
        sp = genetics_mod.setpoints(profile, weight_kg=inp.weight_kg,
                                    day=inp.day if inp.weight_kg is None else None, phase=inp.phase)
        weight = sp.weight_kg
        setpoints = {"profile": sp.profile_id, "phase": sp.phase, "setpoint_c": sp.setpoint_c,
                     "setpoint_meaning": sp.setpoint_meaning, "design_room_temp_c": sp.design_room_temp_c,
                     "rh_pct": sp.rh_pct, "norm_min_m3h": sp.min_vent_m3h, "norm_max_m3h": sp.max_vent_m3h}
        design_t, profile_rh = sp.design_room_temp_c, sp.rh_pct
        sources.append(sp.source_id)
        assumptions += sp.assumptions
        warnings += sp.warnings
    else:  # boar — профилей уставок у хряков нет: только явная температура
        if inp.weight_kg is None or inp.t_inside_c is None:
            raise ValueError("boar needs weight_kg and t_inside_c")
        weight, setpoints, design_t, profile_rh, profile = inp.weight_kg, {}, inp.t_inside_c, None, None

    t_in = design_t if inp.t_inside_c is None else inp.t_inside_c
    if inp.t_inside_c is not None and setpoints:
        assumptions.append(_note("inside_temperature_given", t_inside_c=inp.t_inside_c))
    rh_max = inp.rh_max_pct or profile_rh or DEFAULT_RH_MAX_PCT
    if inp.rh_max_pct is None and profile_rh is None:
        assumptions.append(_note("rh_max_default", rh_pct=DEFAULT_RH_MAX_PCT))

    # ── Минимальная вентиляция ────────────────────────────────────────────────
    genetics_for_norm = profile if inp.group == "growing" else None
    mv = min_ventilation(inp.group, weight, t_in, inp.t_outside_c, inp.rh_outside_pct,
                         rh_max_pct=rh_max, co2_limit_ppm=inp.co2_limit_ppm, co2_outdoor_ppm=inp.co2_outdoor_ppm,
                         norm=inp.norm, altitude_m=inp.altitude_m, method=inp.method,
                         feed_level=inp.feed_level, floor_feed=inp.floor_feed,
                         genetics=genetics_for_norm, phase=inp.phase, **heat_kw)
    assumptions += mv.assumptions
    warnings += mv.warnings
    sources += mv.sources
    heat = mv.heat

    # ── Максимум при текущей наружной температуре ───────────────────────────────────────────────────────
    p_pa = pressure_at_altitude_pa(inp.altitude_m)
    mx = max_ventilation(inp.group, weight, inp.count, inp.t_outside_c, rise_c=inp.rise_c,
                         ua_w_per_k=inp.ua_w_per_k or 0.0, norm=inp.max_norm,
                         genetics=genetics_for_norm, phase=inp.phase, p_pa=p_pa, method=inp.method,
                         feed_level=inp.feed_level, floor_feed=inp.floor_feed, **heat_kw)
    sources += mx.sources
    warnings += [w for w in mx.warnings if w not in warnings]

    # Помещение в режиме отопления: наружная с подъёмом холоднее помещения — расчёт
    # «по теплу» шёл бы при внутренней ниже уставки (на −15 °C — при −12), а норма
    # «на лето» к этому дню не относится. Как в MINMAXapp: на морозе максимум равен
    # минимуму — сверх минимальной вентиляции воздуха не нужно, тепло уходит на её подогрев
    heating_mode = inp.t_outside_c + inp.rise_c < t_in
    if heating_mode:
        assumptions.append(_note("max_equals_min_in_heating", t_outside_c=inp.t_outside_c, t_inside_c=round(t_in, 1)))

    # ── Отопление ─────────────────────────────────────────────────────────────
    heating = None
    if inp.ua_w_per_k is not None or inp.heater_power_kw > 0:
        if inp.ua_w_per_k is None:
            assumptions.append(_note("envelope_not_given"))
        creep_w = inp.creep_heat_w_per_pen * inp.count if inp.group == "lactating_sow" else 0.0
        hb = calculate_heat_balance(
            ua_coefficient=inp.ua_w_per_k or 0.0, q_min_m3h=mv.per_head_m3h * inp.count, t_inside_target=t_in,
            t_outside=inp.t_outside_c, num_animals=inp.count, animal_weight_kg=weight,
            heater_power_kw=inp.heater_power_kw, heater_type=inp.heater_type, p_pa=p_pa,
            animals_sensible_w=heat.sensible_w * inp.count + creep_w,
            co2_limit_ppm=inp.co2_limit_ppm, co2_outdoor_ppm=inp.co2_outdoor_ppm)
        heating = {"heater_required_kw": hb.q_heater_required_kw, "walls_kw": hb.q_walls_kw,
                   "ventilation_kw": hb.q_ventilation_kw, "animals_kw": hb.q_animals_kw,
                   "creep_heat_kw": round(creep_w / 1000, 2), "status_code": hb.status_code,
                   "dilution_air_m3h": hb.dilution_air_m3h}
        if inp.heater_power_kw > 0:
            heating.update(duty_cycle=hb.duty_cycle, heater_covers_need=hb.heater_covers_need,
                           heater_deficit_kw=hb.heater_deficit_kw)
        if inp.group == "lactating_sow" and inp.creep_heat_w_per_pen == 0:
            assumptions.append(_note("creep_heat_not_counted"))

    # ── Эффективная температура ───────────────────────────────────────────────
    eff = None
    if inp.air_speed_ms is not None:
        e = effective_temperature(t_in, inp.air_speed_ms, inp.floor, setpoints.get("setpoint_c") if setpoints else None)
        eff = {"t_effective_c": e.t_effective_c, "wind_chill_c": e.wind_chill_c,
               "floor_correction_c": e.floor_correction_c, "above_setpoint_c": e.above_setpoint_c}
        sources += e.sources
        assumptions += e.assumptions
        warnings += e.warnings

    return Requirement(
        weight_kg=round(weight, 2), t_inside_c=round(t_in, 1), setpoints=setpoints,
        heat={"total_w": heat.total_w, "sensible_w": heat.sensible_w, "latent_w": heat.latent_w,
              "vapour_g_h": heat.vapour_g_h, "co2_l_h": heat.co2_l_h, "method": heat.method},
        min_ventilation={"per_head_m3h": mv.per_head_m3h, "total_m3h": round(mv.per_head_m3h * inp.count, 0),
                         "basis": mv.basis, "co2_m3h": mv.co2_m3h, "moisture_m3h": mv.moisture_m3h,
                         "norm_m3h": mv.norm_m3h, "delta_w_g_per_kg": mv.delta_w_g_per_kg,
                         "humidity_uncontrollable": mv.humidity_uncontrollable},
        max_ventilation=({"per_head_m3h": mv.per_head_m3h, "total_m3h": round(mv.per_head_m3h * inp.count, 0),
                          "basis": "minimum", "heat_m3h": mx.heat_m3h_per_head, "norm_m3h": mx.norm_m3h_per_head,
                          "t_inside_c": round(t_in, 1), "rise_c": mx.rise_c} if heating_mode else
                         {"per_head_m3h": mx.per_head_m3h, "total_m3h": mx.total_m3h, "basis": mx.basis,
                          "heat_m3h": mx.heat_m3h_per_head, "norm_m3h": mx.norm_m3h_per_head,
                          "t_inside_c": mx.t_inside_c, "rise_c": mx.rise_c}),
        heating=heating, effective_temperature=eff,
        sources=list(dict.fromkeys(sources)),
        assumptions=_unique(assumptions), warnings=_unique(warnings),
    )


def _unique(notes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for n in notes:
        if n not in out:
            out.append(n)
    return out
