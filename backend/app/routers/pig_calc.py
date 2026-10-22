"""
pig_calc.py — Main calculation endpoints for PigMaxApp.
"""

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from ..core import sources
from ..core.pig_heat import DEFAULT_FEED_LEVEL, FeedLevel, Group, Method, MethodNotApplicable, pig_heat
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


class VentRequest(BaseModel):
    weight_kg:    float = Field(..., gt=0, le=200, description="Average live weight, kg")
    num_pigs:     int   = Field(..., gt=0, description="Number of pigs in house")
    t_outside_c:  float = Field(..., description="Outside temperature, °C")
    # Literal, а не str: раньше любая опечатка («Winter», «зима») молча
    # считалась летом и давала летнюю уставку
    season:       Literal["winter", "summer"] = Field("winter", description="'winter' | 'summer'")
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


class HeatProductionRequest(BaseModel):
    group:          Group
    weight_kg:      float = Field(..., gt=0, le=400, description="Live weight, kg (lactating sow — the sow alone)")
    t_inside_c:     float = Field(..., ge=-20, le=45)
    method:         Method    = "cigr2002"
    feed_level:     FeedLevel = DEFAULT_FEED_LEVEL
    pregnancy_day:  Optional[float] = Field(None, ge=0, le=120)
    daily_gain_kg:  Optional[float] = Field(None, ge=0, le=1.5)
    milk_kg_day:    Optional[float] = Field(None, ge=0, le=20)

    @model_validator(mode="after")
    def _gestation_day(self):
        # День супоросности меняет тепло к концу срока на ~100 Вт (CIGR ур. 9) —
        # без него расчёт был бы молча не про ту свиноматку
        if self.group == "gestating_sow" and self.pregnancy_day is None:
            raise ValueError("pregnancy_day is required for gestating_sow")
        return self


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

    q_min_total = sp.min_vent_m3_head_h * req.num_pigs
    # Тепло — при уставке выбранного сезона: от температуры помещения зависит и
    # полное тепло (−1.2 %/°C), и его доля явного
    heat = _heat_or_422("growing", req.weight_kg, setpoint, req.heat_method, req.feed_level)

    # Comfort check: if outside > setpoint, suggest increased ventilation
    cooling_needed = req.t_outside_c > setpoint

    return {
        "weight_kg":             req.weight_kg,
        "day_post_weaning":      sp.day_post_weaning,
        "target_temp_c":         sp.target_temp_c,
        "setpoint_c":            setpoint,
        "min_vent_per_head_m3h": sp.min_vent_m3_head_h,
        "min_vent_total_m3h":    round(q_min_total, 0),
        "pig_heat_w_per_head":   heat.total_w,
        "pig_heat_total_kw":     round(heat.total_w * req.num_pigs / 1000, 2),
        "pig_sensible_w_per_head": heat.sensible_w,
        "heat_method":           heat.method,
        "sources":               [heat.source_id, "pic_2019_w2f"],
        "cooling_needed":        cooling_needed,
        "rh_limit_pct":          65,   # PIC: humidity must not exceed 65%
        "gas_limits":            gas_limits(),
        "warnings":              weight_warnings(req.weight_kg) + heat.warnings,
        "assumptions":           heat.assumptions,
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


@router.post("/calc/heat-production")
def calc_heat_production(req: HeatProductionRequest):
    """Heat, vapour and CO₂ of one animal (lactating sow — with her litter)."""
    h = _heat_or_422(req.group, req.weight_kg, req.t_inside_c, req.method, req.feed_level,
                     pregnancy_day=req.pregnancy_day, daily_gain_kg=req.daily_gain_kg,
                     milk_kg_day=req.milk_kg_day)
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
