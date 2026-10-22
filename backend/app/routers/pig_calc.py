"""
pig_calc.py — Main calculation endpoints for PigMaxApp.
"""

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from ..core import sources
from ..core.min_ventilation import (DEFAULT_CO2_LIMIT_PPM, DEFAULT_CO2_OUTDOOR_PPM, DEFAULT_RH_MAX_PCT,
                                    MinVentilation, NormSource, Period, min_ventilation)
from ..core.pig_heat import DEFAULT_FEED_LEVEL, FeedLevel, FloorFeed, Group, Method, MethodNotApplicable, pig_heat
from ..core.pig_profile import TABLE_MAX_KG, TABLE_MIN_KG, interpolate_by_weight
from ..core.pig_thermal  import analyze_heat_balance
from ..core.pig_stocking import stocking_check

router = APIRouter()


# ─── Предельные концентрации газов ────────────────────────────────────────────
# PIC Wean to Finish Guidelines 2019, с. 4-10 (англ. оригинал): «NH3 <20ppm,
# CO2 <3,000ppm, CO <30ppm, H2S <5ppm». В русском переводе (стр. 22) те же числа
# ошибочно подписаны «мг/м³» — отсюда была ошибка единиц (docs/sources.md, 2.1).
GAS_LIMITS_PPM = {"nh3": 20, "co2": 3000, "co": 30, "h2s": 5}

# Молярные массы, г/моль — для пересчёта ppm → мг/м³
_MOLAR_MASS = {"nh3": 17.031, "co2": 44.01, "co": 28.01, "h2s": 34.08}

# Молярный объём идеального газа при 20 °C и 101.325 кПа, л/моль. Нормы
# по рабочей зоне пересчитывают при 20 °C, а не при 0 °C (22.41)
_MOLAR_VOLUME_20C = 24.04


def gas_limits() -> dict:
    """Пределы PIC в ppm и те же пределы в мг/м³ при 20 °C.

    Отдаём оба представления: российские нормы (РД-АПК 1.10.02.04-12) заданы
    в мг/м³ и % об., а газоанализаторы на фермах показывают по-разному.
    """
    return {
        "source": "PIC Wean to Finish Guidelines 2019, p. 4-10",
        "ppm": dict(GAS_LIMITS_PPM),
        "mg_m3_at_20c": {
            gas: round(ppm * _MOLAR_MASS[gas] / _MOLAR_VOLUME_20C, 1)
            for gas, ppm in GAS_LIMITS_PPM.items()
        },
    }


def weight_warnings(weight_kg: float) -> list[dict]:
    """Предупреждение, если вес вне таблицы PIC 4.1 и уставки взяты с её края."""
    if TABLE_MIN_KG <= weight_kg <= TABLE_MAX_KG:
        return []
    return [{
        "code": "weight_outside_table",
        "params": {"weight_kg": weight_kg, "table_min_kg": TABLE_MIN_KG, "table_max_kg": TABLE_MAX_KG},
    }]


class MinVentParams(BaseModel):
    rh_max_pct:       float = Field(DEFAULT_RH_MAX_PCT, gt=30, le=95, description="Upper RH limit inside, %")
    co2_limit_ppm:    float = Field(DEFAULT_CO2_LIMIT_PPM, ge=1000, le=5000)
    co2_outdoor_ppm:  float = Field(DEFAULT_CO2_OUTDOOR_PPM, ge=300, le=600)
    norm:             NormSource = "pic"
    period:           Optional[Period] = None
    altitude_m:       float = Field(0.0, ge=-100, le=4000)
    floor_feed:       FloorFeed = "dry_feed_dry_floor"


# Наружная RH по умолчанию для /calc/ventilation: интерфейс её пока не спрашивает.
# 80 % — типичная зимняя влажность умеренного климата; в ответе это допущение
DEFAULT_OUTSIDE_RH_PCT = 80.0


class VentRequest(MinVentParams):
    weight_kg:    float = Field(..., gt=0, le=200, description="Average live weight, kg")
    num_pigs:     int   = Field(..., gt=0, description="Number of pigs in house")
    t_outside_c:  float = Field(..., description="Outside temperature, °C")
    # Literal, а не str: раньше любая опечатка («Winter», «зима») молча
    # считалась летом и давала летнюю уставку
    season:       Literal["winter", "summer"] = Field("winter", description="'winter' | 'summer'")
    rh_outside_pct: Optional[float] = Field(None, ge=0, le=100)
    heat_method:  Method    = "cigr2002"
    feed_level:   FeedLevel = DEFAULT_FEED_LEVEL


class HeatRequest(BaseModel):
    weight_kg:           float = Field(..., gt=0)
    num_pigs:            int   = Field(..., gt=0)
    t_outside_c:         float
    q_min_m3h:           float = Field(..., gt=0)
    ua_coefficient:      float | None = None
    building_volume_m3:  float | None = None
    total_heater_kw:     float | None = None
    heat_method:         Method    = "cigr2002"
    feed_level:          FeedLevel = DEFAULT_FEED_LEVEL
    floor_feed:          FloorFeed = "dry_feed_dry_floor"


class HeatProductionRequest(BaseModel):
    group:          Group
    weight_kg:      float = Field(..., gt=0, le=400, description="Live weight, kg (lactating sow — the sow alone)")
    t_inside_c:     float = Field(..., ge=-20, le=45)
    method:         Method    = "cigr2002"
    feed_level:     FeedLevel = DEFAULT_FEED_LEVEL
    pregnancy_day:  Optional[float] = Field(None, ge=0, le=120)
    daily_gain_kg:  Optional[float] = Field(None, ge=0, le=1.5)
    milk_kg_day:    Optional[float] = Field(None, ge=0, le=20)
    floor_feed:     FloorFeed = "dry_feed_dry_floor"

    @model_validator(mode="after")
    def _gestation_day(self):
        # День супоросности меняет тепло к концу срока на ~100 Вт (CIGR ур. 9) —
        # без него расчёт был бы молча не про ту свиноматку
        if self.group == "gestating_sow" and self.pregnancy_day is None:
            raise ValueError("pregnancy_day is required for gestating_sow")
        return self


class MinVentRequest(HeatProductionRequest, MinVentParams):
    t_outside_c:     float = Field(..., ge=-50, le=50)
    rh_outside_pct:  float = Field(..., ge=0, le=100)
    num_animals:     int   = Field(1, gt=0)
    litter_mass_kg:  Optional[float] = Field(None, ge=0, le=200, description="Lactating sow: litter mass for the RD-APK norm")


class StockingRequest(BaseModel):
    weight_kg:      float = Field(..., gt=0)
    num_pigs:       int   = Field(..., gt=0)
    floor_area_m2:  float = Field(..., gt=0)
    floor_type:     Literal["slatted", "solid"] = Field("slatted", description="'slatted' | 'solid'")


@router.post("/calc/ventilation")
def calc_ventilation(req: VentRequest):
    """
    Given live weight and outside conditions, return:
    - Target temperature setpoint
    - Minimum ventilation rate (total for the house)
    - Heat production estimate
    """
    sp = interpolate_by_weight(req.weight_kg)

    setpoint = sp.winter_setpoint_c if req.season == "winter" else sp.summer_setpoint_c

    # Минимальная вентиляция = max(норма, влага, CO₂) при уставке выбранного сезона:
    # от температуры помещения зависят тепло, его доля явного и предел влагосодержания
    rh_out = DEFAULT_OUTSIDE_RH_PCT if req.rh_outside_pct is None else req.rh_outside_pct
    mv = _min_vent_or_422("growing", req.weight_kg, setpoint, req.t_outside_c, rh_out, req,
                          method=req.heat_method, feed_level=req.feed_level)
    heat = mv.heat
    assumptions = list(mv.assumptions)
    if req.rh_outside_pct is None:
        assumptions.append({"code": "outside_rh_default", "params": {"rh_pct": DEFAULT_OUTSIDE_RH_PCT}})

    # Comfort check: if outside > setpoint, suggest increased ventilation
    cooling_needed = req.t_outside_c > setpoint

    return {
        "weight_kg":             req.weight_kg,
        "day_post_weaning":      sp.day_post_weaning,
        "target_temp_c":         sp.target_temp_c,
        "setpoint_c":            setpoint,
        "min_vent_per_head_m3h": mv.per_head_m3h,
        "min_vent_total_m3h":    round(mv.per_head_m3h * req.num_pigs, 0),
        "min_vent_basis":        mv.basis,
        "min_vent_breakdown":    _breakdown(mv),
        "humidity_uncontrollable": mv.humidity_uncontrollable,
        "pig_heat_w_per_head":   heat.total_w,
        "pig_heat_total_kw":     round(heat.total_w * req.num_pigs / 1000, 2),
        "pig_sensible_w_per_head": heat.sensible_w,
        "heat_method":           heat.method,
        "sources":               _unique(mv.sources + ["pic_2019_w2f"]),
        "cooling_needed":        cooling_needed,
        "rh_limit_pct":          req.rh_max_pct,
        "gas_limits":            gas_limits(),
        "warnings":              weight_warnings(req.weight_kg) + mv.warnings,
        "assumptions":           assumptions,
    }


@router.post("/calc/heat-balance")
def calc_heat_balance(req: HeatRequest):
    """Full heat balance for pig house heating analysis."""
    sp = interpolate_by_weight(req.weight_kg)

    result = analyze_heat_balance(
        t_inside_target     = sp.target_temp_c,
        t_outside           = req.t_outside_c,
        q_min_m3h           = req.q_min_m3h,
        num_pigs            = req.num_pigs,
        avg_weight_kg       = req.weight_kg,
        ua_coefficient      = req.ua_coefficient,
        building_volume_m3  = req.building_volume_m3,
        total_heater_power_kw = req.total_heater_kw,
        heat_method         = req.heat_method,
        feed_level          = req.feed_level,
        floor_feed          = req.floor_feed,
    )

    result["target_temp_c"] = sp.target_temp_c
    result["setpoint_used_c"] = sp.target_temp_c
    result["warnings"] = weight_warnings(req.weight_kg)
    return result


def _heat_or_422(group, weight_kg, t_c, method, feed_level, **kw):
    try:
        return pig_heat(group, weight_kg, t_c, method=method, feed_level=feed_level, **kw)
    except MethodNotApplicable as e:
        raise HTTPException(status_code=422, detail={"code": "method_not_applicable", "message": str(e)})


def _unique(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def _breakdown(mv: MinVentilation) -> dict:
    """Все три критерия на голову — видно, насколько определяющий опережает остальные."""
    return {"co2_m3h": mv.co2_m3h, "moisture_m3h": mv.moisture_m3h, "norm_m3h": mv.norm_m3h,
            "delta_w_g_per_kg": mv.delta_w_g_per_kg}


def _min_vent_or_422(group, weight_kg, t_in, t_out, rh_out, params: MinVentParams, **kw) -> MinVentilation:
    try:
        return min_ventilation(
            group, weight_kg, t_in, t_out, rh_out,
            rh_max_pct=params.rh_max_pct, co2_limit_ppm=params.co2_limit_ppm,
            co2_outdoor_ppm=params.co2_outdoor_ppm, norm=params.norm, period=params.period,
            altitude_m=params.altitude_m, floor_feed=params.floor_feed, **kw)
    except MethodNotApplicable as e:
        raise HTTPException(status_code=422, detail={"code": "method_not_applicable", "message": str(e)})
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": "invalid_parameters", "message": str(e)})


@router.post("/calc/min-ventilation")
def calc_min_ventilation(req: MinVentRequest):
    """Minimum ventilation per head and per room: max(norm, moisture, CO₂)."""
    mv = _min_vent_or_422(req.group, req.weight_kg, req.t_inside_c, req.t_outside_c, req.rh_outside_pct, req,
                          method=req.method, feed_level=req.feed_level, pregnancy_day=req.pregnancy_day,
                          daily_gain_kg=req.daily_gain_kg, milk_kg_day=req.milk_kg_day,
                          litter_mass_kg=req.litter_mass_kg)
    return {
        "group":            req.group,
        "per_head_m3h":     mv.per_head_m3h,
        "total_m3h":        round(mv.per_head_m3h * req.num_animals, 0),
        "basis":            mv.basis,
        "breakdown":        _breakdown(mv),
        "humidity_uncontrollable": mv.humidity_uncontrollable,
        "heat": {"total_w": mv.heat.total_w, "sensible_w": mv.heat.sensible_w,
                 "vapour_g_h": mv.heat.vapour_g_h, "co2_l_h": mv.heat.co2_l_h, "method": mv.heat.method},
        "sources":          _unique(mv.sources),
        "assumptions":      mv.assumptions,
        "warnings":         mv.warnings,
    }


@router.post("/calc/heat-production")
def calc_heat_production(req: HeatProductionRequest):
    """Heat, vapour and CO₂ of one animal (lactating sow — with her litter)."""
    h = _heat_or_422(req.group, req.weight_kg, req.t_inside_c, req.method, req.feed_level,
                     pregnancy_day=req.pregnancy_day, daily_gain_kg=req.daily_gain_kg,
                     milk_kg_day=req.milk_kg_day, floor_feed=req.floor_feed)
    return {
        "group":       req.group,
        "weight_kg":   req.weight_kg,
        "t_inside_c":  req.t_inside_c,
        "total_w":     h.total_w,
        "sensible_w":  h.sensible_w,
        "latent_w":    h.latent_w,
        "vapour_g_h":  h.vapour_g_h,
        "co2_l_h":     h.co2_l_h,
        "method":      h.method,
        "sources":     [h.source_id],
        "assumptions": h.assumptions,
        "warnings":    h.warnings,
    }


@router.get("/sources/{source_id}")
def get_source(source_id: str):
    """What a source_id in a calculation response stands for."""
    s = sources.get_source(source_id)
    if s is None:
        raise HTTPException(status_code=404, detail={"code": "source_not_found", "source_id": source_id})
    return sources.as_dict(s)


@router.post("/calc/stocking")
def calc_stocking(req: StockingRequest):
    """Stocking density check against PIC Table 5.1."""
    return stocking_check(req.num_pigs, req.floor_area_m2, req.weight_kg, req.floor_type)
