"""
Максимальная (летняя) вентиляция — по балансу явного тепла и по нормам на голову.

По теплу: воздух снаружи нагревается в помещении на ΔT, вынося явное тепло
поголовья; ограждение при t_in > t_out тоже отдаёт тепло наружу:

  Q = (S − UA·ΔT) / (ρ·cp·ΔT)        — обратная к building_thermal.ventilation_heat_w

S — явное тепло на уровне здания при t_in = t_out + ΔT (pig_heat, с поправкой ks).
ΔT по умолчанию 3 °C — допущение; РД-АПК (прим. 3 к табл. 17) допускает до +5 °C
над наружной, но не выше 26–28 °C — задаётся параметром.

По норме на голову (м³/ч):
  pic       — Gilt & Sow 2021 табл. 2.4 (доращивание 40 cfm, откорм и свинки 120–150 → 150),
              табл. 3.1 (осеменение и супоросность 150), табл. 5.2 (опорос летом 650);
              W2F 2019, разд. 4 «Maximum Ventilation Reference»: «120–150 CFM per finishing
              pig» без туннеля — верхний край;
  genetics  — максимум из профиля (Klimaatplatform 2021);
  none      — только тепло.

Итог — большее из двух: вентиляторы ставят под худшее из требований.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from . import genetics as genetics_mod
from .building_thermal import AIR_CP_J_PER_KG_K, air_density_kg_m3
from .min_ventilation import CFM_TO_M3H
from .pig_heat import DEFAULT_FEED_LEVEL, FeedLevel, FloorFeed, Group, Method, PigHeat, pig_heat
from .psychro import P_ATM_PA

MaxNorm = Literal["pic", "genetics", "none"]

DEFAULT_RISE_C = 3.0

# Граница доращивания для нормы PIC: 70 дней по табл. 2.4 ≈ 25 кг (PIC W2F табл. 4.1:
# 44-й день после отъёма — 24 кг, отъём в 3–4 недели)
PIC_NURSERY_MAX_KG = 25.0
PIC_MAX_CFM = {"nursery": 40.0, "growing": 150.0, "gestating_sow": 150.0, "dry_sow": 150.0,
               "boar": 150.0, "lactating_sow": 650.0}


@dataclass(frozen=True)
class MaxVentilation:
    per_head_m3h: float
    total_m3h: float
    basis: str                      # "heat" | "norm"
    heat_m3h_per_head: float
    norm_m3h_per_head: Optional[float]
    t_inside_c: float
    rise_c: float
    heat: PigHeat
    sources: list[str]
    assumptions: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


def _note(code: str, **params) -> dict:
    return {"code": code, "params": params} if params else {"code": code}


def heat_removal_m3h(sensible_w: float, ua_w_per_k: float, t_inside_c: float, rise_c: float,
                     p_pa: float = P_ATM_PA) -> float:
    """Расход, выносящий явное тепло при подъёме rise_c, м³/ч (воздух при t_in — вытяжные вентиляторы)."""
    net = max(sensible_w - ua_w_per_k * rise_c, 0.0)
    return net / (air_density_kg_m3(t_inside_c, p_pa) * AIR_CP_J_PER_KG_K * rise_c) * 3600.0


def max_norm_m3h(norm: MaxNorm, group: str, weight_kg: float, genetics: Optional[str],
                 phase: Optional[str]) -> tuple[Optional[float], Optional[str]]:
    if norm == "none":
        return None, None
    if norm == "genetics":
        if genetics is None or group != "growing":
            raise ValueError("max norm 'genetics' needs a genetics profile and growing pigs")
        sp = genetics_mod.setpoints(genetics, weight_kg=weight_kg, phase=phase)
        if sp.max_vent_m3h is None:
            raise ValueError(f"profile {genetics} has no maximum ventilation per head")
        return sp.max_vent_m3h, sp.source_id
    key = group
    if group == "growing":
        key = "nursery" if weight_kg <= PIC_NURSERY_MAX_KG else "growing"
    source = "pic_2021_sow" if group != "growing" or key == "nursery" else "pic_2019_w2f"
    return round(PIC_MAX_CFM[key] * CFM_TO_M3H, 1), source


def max_ventilation(
    group: Group,
    weight_kg: float,
    num_animals: int,
    t_outside_c: float,
    *,
    rise_c: float = DEFAULT_RISE_C,
    ua_w_per_k: float = 0.0,
    norm: MaxNorm = "pic",
    genetics: Optional[str] = None,
    phase: Optional[str] = None,
    p_pa: float = P_ATM_PA,
    method: Method = "cigr2002",
    feed_level: FeedLevel = DEFAULT_FEED_LEVEL,
    floor_feed: FloorFeed = "dry_feed_dry_floor",
    pregnancy_day: Optional[float] = None,
    daily_gain_kg: Optional[float] = None,
    milk_kg_day: Optional[float] = None,
    lactation_day: Optional[float] = None,
    litter_mass_kg: Optional[float] = None,
) -> MaxVentilation:
    if rise_c <= 0:
        raise ValueError("rise_c must be positive")
    t_in = t_outside_c + rise_c
    heat = pig_heat(group, weight_kg, t_in, method=method, feed_level=feed_level, floor_feed=floor_feed,
                    pregnancy_day=pregnancy_day, daily_gain_kg=daily_gain_kg, milk_kg_day=milk_kg_day,
                    lactation_day=lactation_day, litter_mass_kg=litter_mass_kg)
    q_heat_total = heat_removal_m3h(heat.sensible_w * num_animals, ua_w_per_k, t_in, rise_c, p_pa)
    q_heat = q_heat_total / num_animals

    assumptions = list(heat.assumptions)
    if rise_c == DEFAULT_RISE_C:
        assumptions.append(_note("design_temperature_rise", rise_c=rise_c))
    warnings = list(heat.warnings)
    sources = [heat.source_id]

    q_norm, norm_source = max_norm_m3h(norm, group, weight_kg, genetics, phase)
    if norm_source:
        sources.append(norm_source)

    basis, q = "heat", q_heat
    if q_norm is not None and q_norm > q_heat:
        basis, q = "norm", q_norm

    # РД-АПК прим. 3: внутри не выше 26–28 °C — на расчётном лете это уже не вентиляция,
    # а охлаждение (панели, туннель со скоростью)
    if t_in > 28.0:
        warnings.append(_note("inside_above_rd_apk_limit", t_inside_c=round(t_in, 1), limit_c=28.0))

    return MaxVentilation(
        per_head_m3h=round(q, 1), total_m3h=round(q * num_animals, 0), basis=basis,
        heat_m3h_per_head=round(q_heat, 1),
        norm_m3h_per_head=q_norm, t_inside_c=round(t_in, 1), rise_c=rise_c, heat=heat,
        sources=sources, assumptions=assumptions, warnings=warnings,
    )
