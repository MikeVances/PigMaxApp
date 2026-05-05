"""
pig_profile.py — PIC 2019 Table 4.1 data + interpolation by live weight.

Source: PIC Руководство по работе на доращивании и откорме, 2019.
Table 4.1: Recommended temperature and minimum ventilation setpoints
           (dry slatted floors, solid walls, full house).
"""

from dataclasses import dataclass
import bisect


@dataclass
class PigSetpoint:
    day_post_weaning: int
    avg_weight_kg: float
    target_temp_c: float       # «Нужная температура»
    winter_setpoint_c: float   # Зимняя уставка
    summer_setpoint_c: float   # Летняя уставка
    min_vent_m3_head_h: float  # Минимальная вентиляция, м³/голову/час


# PIC Table 4.1 — mode: no brooders/mats (standard finishing)
# Rows where brooder variants are present, we use the no-brooder row
# as the conservative (higher heat demand) case.
PIC_TABLE_41 = [
    PigSetpoint(1,   5.4,  29.5, 30.5, 29.5, 3.4),
    PigSetpoint(14,  8.2,  27.0, 28.0, 27.0, 3.4),
    PigSetpoint(30,  14.5, 24.0, 23.0, 23.0, 3.7),
    PigSetpoint(44,  24.0, 21.0, 21.0, 20.0, 4.9),
    PigSetpoint(58,  34.0, 19.5, 19.0, 18.0, 6.6),
    PigSetpoint(72,  46.0, 18.0, 17.5, 16.0, 8.7),
    PigSetpoint(86,  58.0, 17.0, 16.0, 15.0, 10.0),
    PigSetpoint(100, 72.0, 16.0, 15.5, 15.0, 12.0),
    PigSetpoint(114, 85.0, 15.0, 15.0, 14.5, 14.4),
    PigSetpoint(128, 98.0, 14.5, 14.5, 14.0, 16.8),
    PigSetpoint(142, 111.0, 14.5, 14.5, 14.0, 19.0),
    PigSetpoint(156, 124.0, 14.5, 14.5, 14.0, 21.4),
    PigSetpoint(170, 135.0, 14.5, 14.5, 14.0, 23.6),
    PigSetpoint(184, 147.0, 14.5, 14.5, 14.0, 24.8),
]

_WEIGHTS = [r.avg_weight_kg for r in PIC_TABLE_41]


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def interpolate_by_weight(weight_kg: float) -> PigSetpoint:
    """
    Linear interpolation of all setpoints for given live weight.
    Clamps to table boundaries (5.4–147 kg).
    """
    weight_kg = max(_WEIGHTS[0], min(_WEIGHTS[-1], weight_kg))

    idx = bisect.bisect_left(_WEIGHTS, weight_kg)

    if idx == 0:
        return PIC_TABLE_41[0]
    if idx >= len(PIC_TABLE_41):
        return PIC_TABLE_41[-1]

    lo = PIC_TABLE_41[idx - 1]
    hi = PIC_TABLE_41[idx]
    t  = (weight_kg - lo.avg_weight_kg) / (hi.avg_weight_kg - lo.avg_weight_kg)

    return PigSetpoint(
        day_post_weaning  = round(_lerp(lo.day_post_weaning,  hi.day_post_weaning,  t)),
        avg_weight_kg     = weight_kg,
        target_temp_c     = round(_lerp(lo.target_temp_c,     hi.target_temp_c,     t), 1),
        winter_setpoint_c = round(_lerp(lo.winter_setpoint_c, hi.winter_setpoint_c, t), 1),
        summer_setpoint_c = round(_lerp(lo.summer_setpoint_c, hi.summer_setpoint_c, t), 1),
        min_vent_m3_head_h= round(_lerp(lo.min_vent_m3_head_h,hi.min_vent_m3_head_h,t), 1),
    )
