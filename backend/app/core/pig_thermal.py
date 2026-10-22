"""
pig_thermal.py — Heat balance for pig houses.

  Q_heater = Q_walls + Q_ventilation − Q_pigs_sensible
  Q_ventilation = Q_min [m³/h] × 0.335 [Wh/m³·K] × ΔT

Тепло свиней — core/pig_heat.py. В обогрев идёт только ЯВНОЕ тепло на уровне
здания: скрытое уходит с паром и воздух не греет. До 2026-10 здесь стояло
5.09·W^0.75 (только поддержание по CIGR) и засчитывалось целиком.
"""

from .pig_heat import DEFAULT_FEED_LEVEL, pig_heat


def ventilation_heat_loss_w(q_min_m3h: float, t_inside: float, t_outside: float) -> float:
    """Heat lost through minimum ventilation airflow, Watts."""
    delta_t = t_inside - t_outside
    # 0.335 W·h/(m³·K) = specific heat capacity of air × density
    return q_min_m3h * 0.335 * delta_t


def analyze_heat_balance(
    t_inside_target: float,
    t_outside: float,
    q_min_m3h: float,
    num_pigs: int,
    avg_weight_kg: float,
    ua_coefficient: float | None = None,
    building_volume_m3: float | None = None,
    total_heater_power_kw: float | None = None,
    heat_method: str = "cigr2002",
    feed_level: str = DEFAULT_FEED_LEVEL,
    floor_feed: str = "dry_feed_dry_floor",
) -> dict:
    """
    Full heat balance for pig house.

    Returns:
        q_pigs_kw         — SENSIBLE heat from pigs (warms the air)
        q_pigs_total_kw   — total heat from pigs, for reference
        q_ventilation_kw  — heat lost through min ventilation
        q_walls_kw        — heat lost through walls/roof (requires UA)
        q_heater_required_kw — heating needed to maintain target temp
        duty_cycle        — fraction of time heater runs (0–1)
        ach_min           — air changes per hour at minimum ventilation
    """
    heat = pig_heat("growing", avg_weight_kg, t_inside_target, method=heat_method,
                    feed_level=feed_level, floor_feed=floor_feed)
    q_pigs_kw = heat.sensible_w * num_pigs / 1000

    delta_t = t_inside_target - t_outside
    q_vent_kw = ventilation_heat_loss_w(q_min_m3h, t_inside_target, t_outside) / 1000

    # Wall losses require UA coefficient from calibration
    q_walls_kw = 0.0
    if ua_coefficient is not None and delta_t > 0:
        q_walls_kw = ua_coefficient * delta_t / 1000  # UA in W/K

    q_heater_required_kw = max(0.0, q_walls_kw + q_vent_kw - q_pigs_kw)

    duty_cycle = None
    if total_heater_power_kw and total_heater_power_kw > 0:
        duty_cycle = min(1.0, q_heater_required_kw / total_heater_power_kw)

    ach_min = None
    if building_volume_m3 and building_volume_m3 > 0:
        ach_min = round(q_min_m3h / building_volume_m3, 2)

    return {
        "q_pigs_kw":            round(q_pigs_kw, 2),
        "q_pigs_total_kw":      round(heat.total_w * num_pigs / 1000, 2),
        "heat_method":          heat.method,
        "heat_source_id":       heat.source_id,
        "heat_assumptions":     heat.assumptions,
        "heat_warnings":        heat.warnings,
        "q_ventilation_kw":     round(q_vent_kw, 2),
        "q_walls_kw":           round(q_walls_kw, 2),
        "q_heater_required_kw": round(q_heater_required_kw, 2),
        "duty_cycle":           round(duty_cycle, 3) if duty_cycle is not None else None,
        "ach_min":              ach_min,
        "pig_heat_fraction":    round(q_pigs_kw / (q_walls_kw + q_vent_kw), 2)
                                if (q_walls_kw + q_vent_kw) > 0 else None,
    }
