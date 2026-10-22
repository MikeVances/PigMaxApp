"""
Утечки и конденсат корпуса — для сценариев «сейчас» и «проект».

Перенесено из MINMAXapp (backend/app/core/ventilation_design.py, 59390c7) по
переписи docs/porting/house_census.md (дополнение этапа 8): помощники утечки при
4 Па и самой холодной поверхности ограждения. Мост: заметки — словари
{code, params}, а не Note MINMAXapp.

  Утечка при 4 Па (ветер, тяга) — нижний предел воздухообмена при отоплении: греть
  приходится не меньше этого воздуха, даже когда минимальная вентиляция меньше.
  К минимуму она не прибавляется — под разрежением щели лишь ещё один впуск.

  Конденсат — внутренняя поверхность ограждения холоднее точки росы воздуха.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .construction import EnvelopeUA, HouseGeometry, envelope_areas, surface_resistance_si
from .leakage_calc import RHO_AIR_KG_M3, _TIGHTNESS_THRESHOLDS
from .psychro import dew_point_c

# Классы UGA → относительная утечка (м²/1000 м² пола) по ВЕРХНЕЙ границе класса:
# в безопасную для обогрева сторону. Границы — те же, что у теста герметичности
# (leakage_calc); у very_leaky верхней границы нет — 1.2 из таблицы UGA 2014
# («0.65–1.2» — третий класс), то есть оценка снизу
AIRTIGHTNESS_CLASS_RELATIVE = {cls: thr for thr, cls in _TIGHTNESS_THRESHOLDS}
AIRTIGHTNESS_CLASS_RELATIVE["very_leaky"] = 1.2
DEFAULT_AIRTIGHTNESS_CLASS = "moderate"

# Давление, при котором утечка греется всегда: ветер и тяга (~4 Па)
NATURAL_LEAK_PRESSURE_PA = 4.0


@dataclass(frozen=True)
class Airtightness:
    """Одно из трёх: класс UGA, относительная утечка, утечка при 4 Па из теста."""
    tightness_class: Optional[str] = None
    relative_leakage: Optional[float] = None
    leak_at_4pa_m3h: Optional[float] = None


def leak_at_4pa_m3h(floor_area_m2: float, airtight: Optional[Airtightness]) -> tuple[float, Optional[str]]:
    # Source: core/ventilation_design.py -> leak_at_4pa_m3h
    """
    Утечка при 4 Па, м³/ч, и класс, если он взят по умолчанию.

    ELA = относительная утечка × площадь пола / 1000; Q = ELA·√(2ΔP/ρ) с Cd = 1 и
    ρ = 1.184 — как в калькуляторе UGA (leakage_calc), иначе класс, определённый
    тестом, и класс, заданный здесь, означали бы разную утечку.
    """
    assumed: Optional[str] = None
    if airtight is not None and airtight.leak_at_4pa_m3h is not None:
        return airtight.leak_at_4pa_m3h, None
    if airtight is not None and airtight.relative_leakage is not None:
        relative = airtight.relative_leakage
    else:
        cls = airtight.tightness_class if airtight is not None and airtight.tightness_class else None
        if cls is None:
            cls = assumed = DEFAULT_AIRTIGHTNESS_CLASS
        relative = AIRTIGHTNESS_CLASS_RELATIVE[cls]
    ela_m2 = relative * floor_area_m2 / 1000.0
    q_m3s = ela_m2 * math.sqrt(2.0 * NATURAL_LEAK_PRESSURE_PA / RHO_AIR_KG_M3)
    return q_m3s * 3600.0, assumed


def coldest_surface(env: Optional[EnvelopeUA], ua_w_per_k: float,
                    g: HouseGeometry) -> tuple[str, float, float]:
    # Source: core/ventilation_design.py -> coldest_surface
    """
    (элемент, U, R_si) с самой холодной внутренней поверхностью: наибольшее U·R_si.
    Измеренный UA по элементам не делится — берём средний U оболочки и R_si стены;
    UA теста включает и щели, так что U завышен — риск виден раньше (безопасно).
    """
    if env is None:
        mean_u = ua_w_per_k / envelope_areas(g).total_m2
        return ("envelope_mean", mean_u, surface_resistance_si("wall"))
    parts = [
        ("side_walls", env.side_walls_u, surface_resistance_si("wall")),
        ("end_walls", env.end_walls_u, surface_resistance_si("wall")),
        ("roof", env.roof_u, surface_resistance_si("roof", env.areas.roof_pitch_deg)),
    ]
    return max(parts, key=lambda p: p[1] * p[2])


def condensation(surface: tuple[str, float, float], t_in: float, rh_in: float,
                 t_out: float) -> Optional[tuple[float, float]]:
    # Source: core/ventilation_design.py -> condensation
    """(T поверхности, T росы), если поверхность холоднее точки росы; иначе None."""
    if t_in <= t_out:
        return None
    _, u, r_si = surface
    t_si = t_in - u * r_si * (t_in - t_out)
    t_dew = dew_point_c(t_in, rh_in)
    return (t_si, t_dew) if t_si < t_dew else None


def airtightness_q4(floor_m2: float, airtight: Optional[Airtightness], assumptions: list) -> float:
    # Source: core/ventilation_design.py -> airtightness_q4 (мост: заметка — словарь)
    q4, assumed_class = leak_at_4pa_m3h(floor_m2, airtight)
    if assumed_class:
        assumptions.append({"code": "airtightness_class_assumed", "params": {
            "class": assumed_class, "relative_leakage": AIRTIGHTNESS_CLASS_RELATIVE[assumed_class]}})
    return q4
