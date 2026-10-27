"""
house_calc.py — расчёты корпуса (этап 4): ограждение, панели, вентиляторы,
максимальная вентиляция, эффективная температура свиней.

Физика ограждения, панелей и вентиляторов перенесена из MINMAXapp
(docs/porting/house_census.md); свиная часть — max_ventilation и pig_effective_temp.
Расчётный климат (Open-Meteo) здесь НЕ отдаётся: внешний запрос тратит
бесплатную квоту — метод появится в API v1 с ключами и лимитами.
"""
from __future__ import annotations

import dataclasses
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from ..core import construction as C
from ..core import evap_cooling_calc as E
from ..core import genetics as genetics_mod
from ..core import tunnel_design as T
from ..core.errors import CalcError
from ..core.max_ventilation import DEFAULT_MAX_NORM, DEFAULT_RISE_C, MaxNorm, max_ventilation
from ..core.heat_stress import heat_stress, lactating_sow_heat_stress
from ..core.pig_effective_temp import Floor, effective_temperature
from ..core.pig_heat import DEFAULT_FEED_LEVEL, FeedLevel, FloorFeed, Group, Method, MethodNotApplicable
from ..core.psychro import pressure_at_altitude_pa

router = APIRouter()


def _calc_error(e: CalcError):
    raise HTTPException(status_code=422, detail=e.detail)


# ─── Ограждение ───────────────────────────────────────────────────────────────

class LayerIn(BaseModel):
    thickness_mm: float = Field(..., gt=0, le=2000)
    material_id: Optional[str] = None
    lambda_w_mk: Optional[float] = None


class SurfaceIn(BaseModel):
    layers: list[LayerIn] = []
    u_w_m2k: Optional[float] = Field(None, gt=0, le=10)

    def to_core(self) -> C.Surface:
        return C.Surface(layers=tuple(C.Layer(**l.model_dump()) for l in self.layers), u_w_m2k=self.u_w_m2k)


class EnvelopeRequest(BaseModel):
    length_m: float = Field(..., gt=0, le=500)
    width_m: float = Field(..., gt=0, le=100)
    wall_height_m: float = Field(..., gt=0, le=20)
    ridge_height_m: Optional[float] = Field(None, gt=0, le=30)
    walls: SurfaceIn
    roof: SurfaceIn
    end_walls: Optional[SurfaceIn] = None


@router.get("/construction-materials")
def construction_materials():
    """Материалы справочника λ (BR 443 §3); утеплитель — λ из паспорта в слое."""
    return [dataclasses.asdict(m) for m in C.MATERIALS.values()]


@router.post("/calc/envelope")
def calc_envelope(req: EnvelopeRequest):
    """UA оболочки (стены, торцы, кровля) по слоям или по U из декларации."""
    g = C.HouseGeometry(req.length_m, req.width_m, req.wall_height_m, req.ridge_height_m)
    try:
        ua = C.envelope_ua(g, req.walls.to_core(), req.roof.to_core(),
                           req.end_walls.to_core() if req.end_walls else None)
    except CalcError as e:
        _calc_error(e)
    return {
        "ua_w_per_k": round(ua.ua_w_per_k, 1), "mean_u_w_m2k": round(ua.mean_u_w_m2k, 3),
        "u_w_m2k": {"side_walls": round(ua.side_walls_u, 3), "end_walls": round(ua.end_walls_u, 3),
                    "roof": round(ua.roof_u, 3)},
        "areas_m2": {"side_walls": round(ua.areas.side_walls_m2, 1), "end_walls": round(ua.areas.end_walls_m2, 1),
                     "roof": round(ua.areas.roof_m2, 1), "total": round(ua.areas.total_m2, 1)},
        "roof_pitch_deg": round(ua.areas.roof_pitch_deg, 1),
        "sources": ["br443_2019_s3", "br443_2019_s4_3"],
    }


# ─── Панельное охлаждение ─────────────────────────────────────────────────────

class EvapRequest(BaseModel):
    house_length_m: float = Field(..., gt=0)
    t_outdoor_db_c: float = Field(..., ge=-10, le=55)
    rh_outdoor_pct: float = Field(..., ge=1, le=100)
    total_fan_m3h: float = Field(..., gt=0)
    total_fan_25pa_m3h: float = Field(0.0, ge=0)
    pad_type: Literal["7090", "7060", "5090"] = "7090"
    pad_thickness_mm: int = 150
    pad_length_m: float = Field(..., gt=0)
    pad_height_m: float = Field(..., gt=0, le=5)
    t_inside_target_c: float = Field(..., ge=5, le=40)
    num_animals: int = Field(0, ge=0)
    animal_weight_kg: float = Field(0.0, ge=0, le=200)
    water_supply_t_c: Optional[float] = None
    pad_efficiency_pct: Optional[float] = Field(None, gt=0, le=100)
    altitude_m: float = Field(0.0, ge=-100, le=4000)

    @model_validator(mode="after")
    def _thickness(self):
        if self.pad_thickness_mm not in E.SANHE_PADS[self.pad_type]["thicknesses"]:
            raise ValueError(f"pad_thickness_mm must be one of {E.SANHE_PADS[self.pad_type]['thicknesses']}")
        return self


@router.post("/calc/evap-cooling")
def calc_evap_cooling(req: EvapRequest):
    """Панельное охлаждение: температура за панелью и у вентиляторов, скорость, вода."""
    data = req.model_dump()
    p_pa = pressure_at_altitude_pa(data.pop("altitude_m"))
    r = E.calculate_evap_cooling(E.EvapCoolingInput(**data, p_pa=p_pa))
    return {**dataclasses.asdict(r), "sources": ["sanhe_pads", "uga_pad_velocity", "ashrae_2017_ch1"]}


# ─── Вентиляторы туннеля ──────────────────────────────────────────────────────

class FanSelectRequest(BaseModel):
    target_m3h: float = Field(..., gt=0)
    section_m2: float = Field(..., gt=0, description="Поперечное сечение корпуса")
    fan_curve: list[tuple[float, float]] = Field(..., min_length=2, description="(Па, м³/ч) по каталогу")
    pad_type: Optional[Literal["7090", "7060", "5090"]] = "7090"
    pad_thickness_mm: int = 150
    pad_area_m2: Optional[float] = Field(None, gt=0, description="Нет — по проектной скорости UGA")
    measured_pressure: Optional[tuple[float, float]] = Field(None, description="Замер (м³/ч, Па)")


@router.post("/calc/tunnel-fans")
def calc_tunnel_fans(req: FanSelectRequest):
    """Число вентиляторов в рабочей точке с сопротивлением корпуса и панелей."""
    pads = T.Pads(req.pad_type, req.pad_thickness_mm) if req.pad_type else None
    area = req.pad_area_m2 or (T.pad_area_m2(req.target_m3h, pads) if pads else None)
    try:
        res = T.Resistance(req.section_m2, pads, area, req.measured_pressure)
        sel = T.select_fans(tuple(req.fan_curve), res, req.target_m3h)
    except CalcError as e:
        _calc_error(e)
    parts = res.parts(sel.flow_per_fan_m3h * sel.count)
    warnings = []
    if parts.basis == "estimated":
        warnings.append({"code": "tunnel_length_loss_not_included"})
    if sel.operating_pressure_pa > T.HIGH_TUNNEL_PRESSURE_PA:
        warnings.append({"code": "tunnel_pressure_high", "params": {"pa": round(sel.operating_pressure_pa, 1)}})
    if sel.extrapolated:
        warnings.append({"code": "fan_curve_extrapolated"})
    return {
        "fans": sel.count, "flow_per_fan_m3h": round(sel.flow_per_fan_m3h, 0),
        "operating_pressure_pa": round(sel.operating_pressure_pa, 1),
        "pressure_parts_pa": {k: round(v, 1) if isinstance(v, float) else v for k, v in dataclasses.asdict(parts).items()},
        "pad_area_m2": round(area, 1) if area else None,
        "sources": ["amca_210", "sanhe_pads", "uga_pad_velocity"], "warnings": warnings,
    }


# ─── Максимальная вентиляция ──────────────────────────────────────────────────

class MaxVentRequest(BaseModel):
    group: Group
    weight_kg: float = Field(..., gt=0, le=400)
    num_animals: int = Field(..., gt=0)
    t_outside_c: float = Field(..., ge=-10, le=50)
    rise_c: float = Field(DEFAULT_RISE_C, gt=0, le=15)
    ua_w_per_k: float = Field(0.0, ge=0)
    norm: MaxNorm = DEFAULT_MAX_NORM
    genetics: Optional[str] = None
    phase: Optional[genetics_mod.Phase] = None
    altitude_m: float = Field(0.0, ge=-100, le=4000)
    method: Method = "cigr2002"
    feed_level: FeedLevel = DEFAULT_FEED_LEVEL
    floor_feed: FloorFeed = "dry_feed_dry_floor"
    pregnancy_day: Optional[float] = Field(None, ge=0, le=120)
    milk_kg_day: Optional[float] = Field(None, ge=0, le=20)

    @model_validator(mode="after")
    def _check(self):
        if self.group == "gestating_sow" and self.pregnancy_day is None:
            raise ValueError("pregnancy_day is required for gestating_sow")
        if self.genetics is not None and genetics_mod.find(self.genetics) is None:
            raise ValueError(f"unknown genetics profile: {self.genetics}")
        return self


@router.post("/calc/max-ventilation")
def calc_max_ventilation(req: MaxVentRequest):
    """Летняя вентиляция: по выносу явного тепла и по норме на голову — большее."""
    try:
        r = max_ventilation(req.group, req.weight_kg, req.num_animals, req.t_outside_c,
                            rise_c=req.rise_c, ua_w_per_k=req.ua_w_per_k, norm=req.norm,
                            genetics=req.genetics, phase=req.phase,
                            p_pa=pressure_at_altitude_pa(req.altitude_m), method=req.method,
                            feed_level=req.feed_level, floor_feed=req.floor_feed,
                            pregnancy_day=req.pregnancy_day, milk_kg_day=req.milk_kg_day)
    except MethodNotApplicable as e:
        raise HTTPException(status_code=422, detail={"code": "method_not_applicable", "message": str(e)})
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": "invalid_parameters", "message": str(e)})
    return {
        "per_head_m3h": r.per_head_m3h, "total_m3h": r.total_m3h, "basis": r.basis,
        "breakdown": {"heat_m3h": r.heat_m3h_per_head, "norm_m3h": r.norm_m3h_per_head},
        "t_inside_c": r.t_inside_c, "rise_c": r.rise_c,
        "sources": list(dict.fromkeys(r.sources)), "assumptions": r.assumptions, "warnings": r.warnings,
    }


# ─── Эффективная температура ──────────────────────────────────────────────────

class EffectiveTempRequest(BaseModel):
    t_air_c: float = Field(..., ge=-30, le=50)
    air_speed_ms: float = Field(0.0, ge=0, le=6)
    floor: Floor = "concrete_slats"
    setpoint_c: Optional[float] = Field(None, ge=5, le=40)
    # Оба заданы — заодно тепловой стресс растущих (Huynh 2005) при эффективной температуре
    rh_pct: Optional[float] = Field(None, ge=1, le=100)
    weight_kg: Optional[float] = Field(None, gt=0, le=400)
    # Растущие (Huynh: нужны RH и вес) или подсосные (Quiniou & Noblet: только температура)
    group: Literal["growing", "lactating_sow"] = "growing"


@router.post("/calc/effective-temperature")
def calc_effective_temperature(req: EffectiveTempRequest):
    """Эффективная температура у свиней: воздух, скорость (McFarlane), пол (Close)."""
    r = effective_temperature(req.t_air_c, req.air_speed_ms, req.floor, req.setpoint_c)
    out = dataclasses.asdict(r)
    notes: list[dict] = []
    if req.group == "lactating_sow":
        out["heat_stress"] = lactating_sow_heat_stress(r.t_effective_c, "effective", notes, out["warnings"], req.rh_pct)
        out["sources"] = out["sources"] + ["quiniou_noblet_1999"]
    elif req.rh_pct is not None and req.weight_kg is not None:
        out["heat_stress"] = heat_stress(r.t_effective_c, req.rh_pct, req.weight_kg, "effective", notes, out["warnings"])
        out["sources"] = out["sources"] + ["huynh_2005"]
    out["assumptions"] = out["assumptions"] + notes
    return out


# ─── Тест герметичности ───────────────────────────────────────────────────────

class LeakageRequest(BaseModel):
    house_length_m: float = Field(..., gt=0, le=500)
    house_width_m: float = Field(..., gt=0, le=100)
    fan_capacity_m3h: float = Field(..., gt=0, description="Тестовые вентиляторы при 25 Па")
    measured_pressure_pa: float = Field(..., gt=0, le=150)
    extra_points: list[tuple[float, float]] = Field([], max_length=6, description="(расход при 25 Па, давление)")


@router.post("/calc/leakage")
def calc_leakage(req: LeakageRequest):
    """Тест герметичности UGA: ELA, относительная утечка, класс; с точками — утечка при 4 Па."""
    from ..core import leakage_calc as L
    try:
        r = L.calculate_leakage(L.LeakageInput(req.house_length_m, req.house_width_m, req.fan_capacity_m3h,
                                               req.measured_pressure_pa, tuple(map(tuple, req.extra_points))))
    except CalcError as e:
        _calc_error(e)
    return {**dataclasses.asdict(r), "sources": ["uga_leakage"]}
