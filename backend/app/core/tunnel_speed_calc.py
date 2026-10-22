"""
Перенесено из MINMAXapp (backend/app/core/tunnel_speed_calc.py, 59390c7) по переписи
docs/porting/house_census.md — ТОЛЬКО часть, не зависящая от вида животного:
коэффициенты впуска, стандартный воздух паспортов и рабочая точка вентиляторов.

Эффективная температура ПТИЦЫ (таблица по скорости воздуха, THVI, скорости по
возрасту бройлеров Cobb) не перенесена (SKIP): у свиней свой аналог — модуль
pig_effective_temp.py (McFarlane 2000, Close 1987).
"""
from __future__ import annotations

import math

from .evap_cooling_calc import _pad_pressure_pa

# Давление второй точки паспорта вентилятора, Па (0.1" вод. ст. ≈ 25 Па — типовая точка BESS)
FAN_RATING_POINT_PA = 25.0
# Стандартный воздух паспортов вентиляторов и таблиц панелей (AMCA 210), кг/м³
RHO_STD_AIR = 1.2

CD_INLET      = 0.62  # Коэффициент расхода для шторных/сетчатых клапанов
CD_INLET_OPEN = 0.90  # Открытый торец (лёгкое сжатие струи + сетка)


def _fan_operating_flow_m3s(
    q0_m3s: float, q25_m3s: float, num_fans: int, resistance_pa_per_m3s2: float
) -> float:
    # Source: core/tunnel_speed_calc.py -> _fan_operating_flow_m3s
    """
    Расход ОДНОГО вентилятора в рабочей точке, м³/с.

    Характеристика вентилятора (линейно по двум точкам паспорта): q = q₀ − s·P,
      s = (q₀ − q₂₅) / 25 Па.
    Сопротивление системы: P = c · (N·q)².
    Подстановка: s·c·N²·q² + q − q₀ = 0 → q = (−1 + √(1 + 4·s·c·N²·q₀)) / (2·s·c·N²).
    """
    s = (q0_m3s - q25_m3s) / FAN_RATING_POINT_PA
    a = s * resistance_pa_per_m3s2 * num_fans ** 2
    if a <= 0:
        return q0_m3s
    return (-1.0 + math.sqrt(1.0 + 4.0 * a * q0_m3s)) / (2.0 * a)


def _operating_flow_with_pad_m3s(q0_m3s: float, q25_m3s: float, num_fans: int,
                                 resistance_pa_per_m3s2: float, pad_area_m2: float,
                                 velocities: list[float], dp_table: list[float]) -> float:
    # Source: core/tunnel_speed_calc.py -> _operating_flow_with_pad_m3s
    """
    Расход СИСТЕМЫ вентиляторов в рабочей точке, когда на впуске стоят панели, м³/с.

    Сопротивление P(Q) = c·Q² + ΔP_панели(Q / A_панели): у панели оно задано кривой
    производителя (SANHE), не квадратом, — замкнутой формулы нет, поэтому бисекция,
    как в инструменте панелей: g(Q) = N·(q₀ − s·P(Q)) − Q убывает, g(0) = N·q₀ > 0.
    Без панели в сопротивлении скорость в корпусе с панелями была бы завышена:
    150 мм при 1.8 м/с — ещё ~40 Па (аудит MINMAXapp 2026-10-18).
    """
    s = (q0_m3s - q25_m3s) / FAN_RATING_POINT_PA
    lo, hi = 0.0, num_fans * q0_m3s
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        p = resistance_pa_per_m3s2 * mid ** 2 + _pad_pressure_pa(velocities, dp_table, mid / pad_area_m2)
        if num_fans * (q0_m3s - s * p) - mid > 0:
            lo = mid
        else:
            hi = mid
    return lo
