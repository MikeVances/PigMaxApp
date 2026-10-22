"""
Тепловой стресс растущих свиней — пороги Huynh et al. 2005.

Источник — аннотация J Anim Sci 83:1385–1396 (PMID 15890816, сохранена в
refs/norms_cooling/huynh_2005_pubmed_abstract.txt; полный текст не прочитан).
Опыт: свинки 58–75 кг, группы по 10 в камерах, корм вволю, температура росла на
2 °C в сутки от 16 до 32 °C при RH 50 / 65 / 80 %. Порог — «температура перегиба»:
выше неё показатель начинает меняться.

  частота дыхания   21.3–23.4 °C  (в выводах 21.3–22.4 — расхождение внутри аннотации)
  теплопродукция    22.9 °C
  ректальная темп.  24.6–27.1 °C
  потребление корма 25.5 °C; ниже на 81 / 99 / 106 г/(гол·сут) на каждый °C сверх
                    порога при RH 50 / 65 / 80 %

Для дыхания и ректальной температуры аннотация говорит лишь, что порог НАИМЕНЬШИЙ
при 80 % RH, но не даёт значения для каждой влажности. Поэтому берём нижнюю
границу диапазона при любой RH — раньше в безопасную сторону (INFERRED). Порог
корма и теплопродукции одинаков для всех RH (CONFIRMED аннотацией).

Температура в опыте — воздуха в камере без обдува. Мы подставляем эффективную
температуру, если известна скорость воздуха: обдув свиньи «чувствуют» как
снижение температуры (McFarlane) — это перенос, а не результат Huynh.
"""
from __future__ import annotations

from typing import Optional

from .psychro import P_ATM_PA, _EPS, sat_pressure_pa

# Пороги, °C — по возрастанию; нижние границы диапазонов (см. выше)
THRESHOLDS_C = {
    "respiration": 21.3,
    "heat_production": 22.9,
    "rectal_temperature": 24.6,
    "feed_intake": 25.5,
}

# Потеря корма на °C сверх порога, г/(гол·сут), по RH опыта
FEED_LOSS_G_PER_C = ((50.0, 81.0), (65.0, 99.0), (80.0, 106.0))

# Диапазон веса опыта: начальный минимум и конечный максимум групп
STUDY_WEIGHT_KG = (58.0, 74.7)
# Верх температур опыта: дальше наклон потери корма — линейная экстраполяция
STUDY_MAX_T_C = 32.0


def _note(code: str, **params) -> dict:
    return {"code": code, "params": params} if params else {"code": code}


def feed_loss_slope_g_per_c(rh_pct: float) -> float:
    """Наклон потери корма по RH — линейно между точками опыта, вне 50–80 % — край."""
    pts = FEED_LOSS_G_PER_C
    if rh_pct <= pts[0][0]:
        return pts[0][1]
    for (h0, s0), (h1, s1) in zip(pts, pts[1:]):
        if rh_pct <= h1:
            return s0 + (s1 - s0) * (rh_pct - h0) / (h1 - h0)
    return pts[-1][1]


def rh_at(t_c: float, w: float, p_pa: float = P_ATM_PA) -> float:
    """RH, %, воздуха с влагосодержанием w при температуре t — обращение humidity_ratio."""
    p_w = w * p_pa / (_EPS + w)
    return min(100.0, 100.0 * p_w / sat_pressure_pa(t_c))


def heat_stress(t_c: float, rh_pct: float, weight_kg: float, temperature_basis: str,
                assumptions: list, warnings: list) -> dict:
    """
    Какие пороги пройдены и сколько корма свинья недоест, г/сут.

    temperature_basis — что подставлено: "effective" (воздух с обдувом и полом)
    или "air" (воздух без обдува). Заметки пишутся в переданные списки.
    """
    exceeded = [name for name, thr in THRESHOLDS_C.items() if t_c > thr]
    over = t_c - THRESHOLDS_C["feed_intake"]
    slope = feed_loss_slope_g_per_c(rh_pct)
    loss = round(slope * over) if over > 0 else 0

    lo, hi = STUDY_WEIGHT_KG
    if not lo <= weight_kg <= hi:
        # Порог зависит от веса (тяжёлые свиньи страдают раньше), а опыт — только 60 кг
        assumptions.append(_note("heat_stress_weight_extrapolated", weight_kg=round(weight_kg, 1),
                                 study_min_kg=lo, study_max_kg=hi))
    if not 50.0 <= rh_pct <= 80.0:
        assumptions.append(_note("heat_stress_rh_outside_study", rh_pct=round(rh_pct)))
    if t_c > STUDY_MAX_T_C:
        assumptions.append(_note("heat_stress_beyond_study_range", t_c=round(t_c, 1), study_max_c=STUDY_MAX_T_C))
    if over > 0:
        warnings.append(_note("heat_stress_feed_intake", t_c=round(t_c, 1),
                              threshold_c=THRESHOLDS_C["feed_intake"], feed_loss_g_day=loss))

    return {"t_c": round(t_c, 1), "temperature_basis": temperature_basis, "rh_pct": round(rh_pct),
            "thresholds_c": dict(THRESHOLDS_C), "exceeded": exceeded, "feed_loss_g_day": loss}
