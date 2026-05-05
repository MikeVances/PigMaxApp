"""
pig_calc.py — Main calculation endpoints for PigMaxApp.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from ..core.pig_profile import interpolate_by_weight
from ..core.pig_thermal  import analyze_heat_balance, pig_heat_w
from ..core.pig_stocking import stocking_check

router = APIRouter()


class VentRequest(BaseModel):
    weight_kg:    float = Field(..., gt=0, le=200, description="Average live weight, kg")
    num_pigs:     int   = Field(..., gt=0, description="Number of pigs in house")
    t_outside_c:  float = Field(..., description="Outside temperature, °C")
    season:       str   = Field("winter", description="'winter' | 'summer'")


class HeatRequest(BaseModel):
    weight_kg:           float = Field(..., gt=0)
    num_pigs:            int   = Field(..., gt=0)
    t_outside_c:         float
    q_min_m3h:           float = Field(..., gt=0)
    ua_coefficient:      float | None = None
    building_volume_m3:  float | None = None
    total_heater_kw:     float | None = None


class StockingRequest(BaseModel):
    weight_kg:      float = Field(..., gt=0)
    num_pigs:       int   = Field(..., gt=0)
    floor_area_m2:  float = Field(..., gt=0)
    floor_type:     str   = Field("slatted", description="'slatted' | 'solid'")


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
    q_pig_w     = pig_heat_w(req.weight_kg)

    # Comfort check: if outside > setpoint, suggest increased ventilation
    cooling_needed = req.t_outside_c > setpoint

    return {
        "weight_kg":             req.weight_kg,
        "day_post_weaning":      sp.day_post_weaning,
        "target_temp_c":         sp.target_temp_c,
        "setpoint_c":            setpoint,
        "min_vent_per_head_m3h": sp.min_vent_m3_head_h,
        "min_vent_total_m3h":    round(q_min_total, 0),
        "pig_heat_w_per_head":   round(q_pig_w, 1),
        "pig_heat_total_kw":     round(q_pig_w * req.num_pigs / 1000, 2),
        "cooling_needed":        cooling_needed,
        "rh_limit_pct":          65,   # PIC: humidity must not exceed 65%
        # Gas limits per PIC guide
        "gas_limits": {
            "nh3_mg_m3":  20,
            "co2_mg_m3":  3000,
            "co_mg_m3":   30,
            "h2s_mg_m3":  5,
        },
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
    )

    result["target_temp_c"] = sp.target_temp_c
    result["setpoint_used_c"] = sp.target_temp_c
    return result


@router.post("/calc/stocking")
def calc_stocking(req: StockingRequest):
    """Stocking density check against PIC Table 5.1."""
    if req.floor_type not in ("slatted", "solid"):
        raise HTTPException(status_code=400, detail="floor_type must be 'slatted' or 'solid'")
    return stocking_check(req.num_pigs, req.floor_area_m2, req.weight_kg, req.floor_type)
