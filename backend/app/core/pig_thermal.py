"""
pig_thermal.py — тепловой баланс для /calc/heat-balance (интерфейс PigMaxApp).

Тонкий переходник над перенесённым из MINMAXapp балансом
(building_thermal.calculate_heat_balance): поля ответа — прежние, их читает
интерфейс. До этапа 4 здесь была упрощённая модель: вентиляционные потери через
константу 0.335 Вт·ч/(м³·К) (ρ·cp при ~+15 °C) и без воздуха горелок открытого
пламени. Теперь — ρ при температуре помещения и загрузка горелок, согласованная с
воздухом на разбавление их CO₂ (solve_heater_duty).

Тепло свиней — core/pig_heat.py. В обогрев идёт только ЯВНОЕ тепло на уровне
здания: скрытое уходит с паром и воздух не греет.
"""

from .building_thermal import calculate_heat_balance
from .pig_heat import DEFAULT_FEED_LEVEL, pig_heat

# Тип отопителя в настройках PigMaxApp — «gas» или «electric», без различия
# открытого пламени и закрытой камеры. Газовый считаем открытым пламенем: его CO₂
# и пар идут в помещение и требуют воздуха — в безопасную сторону для Qmin и загрузки
HEATER_TYPE_MAP = {"gas": "gas_open", "electric": "electric", "gas_open": "gas_open"}


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
    heater_type: str = "gas",
) -> dict:
    """
    Тепловой баланс свинарника.

    Returns:
        q_pigs_kw            — ЯВНОЕ тепло свиней (греет воздух)
        q_pigs_total_kw      — полное тепло, справочно
        q_ventilation_kw     — потери с воздухом (вместе с воздухом горелок)
        q_walls_kw           — потери через ограждения (нужен UA)
        q_heater_required_kw — нужно от обогревателей
        duty_cycle           — загрузка обогревателей 0–1 (None без их мощности)
        ach_min              — кратность воздухообмена при минимальной вентиляции
    """
    heat = pig_heat("growing", avg_weight_kg, t_inside_target, method=heat_method,
                    feed_level=feed_level, floor_feed=floor_feed)
    hb = calculate_heat_balance(
        ua_coefficient=ua_coefficient or 0.0,
        q_min_m3h=q_min_m3h,
        t_inside_target=t_inside_target,
        t_outside=t_outside,
        num_animals=num_pigs,
        animal_weight_kg=avg_weight_kg,
        heater_power_kw=total_heater_power_kw or 0.0,
        heater_type=HEATER_TYPE_MAP.get(heater_type, "gas_open"),
        animals_sensible_w=heat.sensible_w * num_pigs,
    )

    assumptions = list(heat.assumptions)
    if heater_type == "gas":
        assumptions.append({"code": "gas_heater_treated_as_open_flame"})

    ach_min = None
    if building_volume_m3 and building_volume_m3 > 0:
        ach_min = round(q_min_m3h / building_volume_m3, 2)

    return {
        "q_pigs_kw":            hb.q_animals_kw,
        "q_pigs_total_kw":      round(heat.total_w * num_pigs / 1000, 2),
        "q_ventilation_kw":     hb.q_ventilation_kw,
        "q_walls_kw":           hb.q_walls_kw,
        "q_heater_required_kw": hb.q_heater_required_kw,
        "duty_cycle":           hb.duty_cycle if total_heater_power_kw else None,
        "heater_covers_need":   hb.heater_covers_need if total_heater_power_kw else None,
        "heater_deficit_kw":    hb.heater_deficit_kw,
        "dilution_air_m3h":     hb.dilution_air_m3h,
        "status_code":          hb.status_code,
        "status_level":         hb.status_level,
        "ach_min":              ach_min,
        "pig_heat_fraction":    round(hb.animal_heat_fraction, 2) if hb.q_total_loss_kw > 0 else None,
        "heat_method":          heat.method,
        "heat_source_id":       heat.source_id,
        "heat_assumptions":     assumptions,
        "heat_warnings":        heat.warnings,
    }
