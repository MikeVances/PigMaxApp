"""
Тепловой баланс свинарника и работа газовых обогревателей.

Перенесено из MINMAXapp (backend/app/core/building_thermal.py, 59390c7) по
переписи docs/porting/house_census.md. Мосты (ADAPT): тепло свиней (pig_heat)
вместо птицы; давление площадки — явным p_pa вместо настроек хозяйства.
Калибровка UA по тесту остывания и её хранение по корпусам НЕ перенесены (SKIP).

Тепловой баланс (уравнение установившегося теплообмена):
═══════════════════════════════════════════════════════════════════════════

  Q_heater + Q_animals  =  Q_walls + Q_vent

  Q_heater  — тепло от обогревателей [Вт]
  Q_animals — явное тепло свиней на уровне здания [Вт]
  Q_walls   — теплопотери через ограждения = UA × (T_in − T_out)  [Вт]
  Q_vent    — теплопотери с вентиляционным воздухом               [Вт]
            = Q_min [м³/с] × ρ × cp × (T_in − T_out)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .pig_heat import pig_heat
from .psychro import P_ATM_PA, dew_point_c


@dataclass
class BuildingDimensions:
    """Размеры помещения"""
    length_m: float  # длина, м
    width_m: float   # ширина, м
    height_m: float  # высота, м

    @property
    def volume_m3(self) -> float:
        # Source: core/building_thermal.py -> BuildingDimensions.volume_m3
        """Объем помещения, м³"""
        return self.length_m * self.width_m * self.height_m

    @property
    def floor_area_m2(self) -> float:
        # Source: core/building_thermal.py -> BuildingDimensions.floor_area_m2
        """Площадь пола, м²"""
        return self.length_m * self.width_m


# Средний U оболочки (стены + кровля + торцы, без пола), Вт/(м²·К). Физический
# диапазон по теплопроводности: сэндвич PIR 100 мм ≈ 0.21 … одинарная штора ≈ 5.9
# (ASHRAE Fundamentals, гл. 26: R = Σ d/λ + R_si + R_se). Коридор шире — тест
# в закрытом корпусе захватывает и инфильтрацию через щели.
U_ENVELOPE_MIN_W_M2K = 0.1
U_ENVELOPE_MAX_W_M2K = 8.0


def envelope_area_m2(b: "BuildingDimensions") -> float:
    # Source: core/building_thermal.py -> envelope_area_m2
    """Стены, кровля и торцы, м². Пол не входит: теплообмен с ним идёт через грунт и массу."""
    return b.length_m * b.width_m + 2.0 * (b.length_m + b.width_m) * b.height_m


# Сопротивление теплоотдаче у внутренней поверхности, м²·К/Вт: 0.13 — стены,
# горизонтальный поток (ASHRAE Fundamentals гл. 26; то же значение в ISO 6946).
# Для кровли (поток вверх) 0.10 — меньше риск; берём 0.13, в безопасную сторону
R_SI_M2K_W = 0.13


def surface_condensation(ua_w_per_k: float, building: "BuildingDimensions",
                         t_inside_c: float, rh_inside_pct: float, t_outside_c: float) -> tuple[float, float, bool]:
    # Source: core/building_thermal.py -> surface_condensation
    """
    Риск конденсата на ограждениях: (T внутренней поверхности, T росы внутри, риск).

    Средний U оболочки = UA / площадь (стены, кровля, торцы). Через ограждение идёт
    тот же поток, что и от воздуха к поверхности: U·ΔT = (T_in − T_si) / R_si, откуда
      T_si = T_in − U·R_si·(T_in − T_out).
    Ниже точки росы внутреннего воздуха — на стенах и кровле конденсат: капель с
    кровли на свиней и мокрые стены. UA с инфильтрацией через щели завышает U —
    оценка в безопасную сторону (риск — раньше).
    """
    u = ua_w_per_k / envelope_area_m2(building)
    t_si = t_inside_c - u * R_SI_M2K_W * (t_inside_c - t_outside_c)
    t_dew = dew_point_c(t_inside_c, rh_inside_pct)
    return t_si, t_dew, t_si < t_dew


AIR_CP_J_PER_KG_K = 1006.0   # удельная теплоёмкость сухого воздуха


def air_density_kg_m3(t_c: float, p_pa: float = P_ATM_PA) -> float:
    # Source: core/building_thermal.py -> air_density_kg_m3 (ADAPT: p явно)
    """
    Плотность сухого воздуха на площадке: ρ = 353.05 / T[K] при 101.325 кПа,
    пересчитанная на давление по высоте площадки.
    """
    return 353.05 / (t_c + 273.15) * p_pa / P_ATM_PA


def heater_air_demand(heater_power_kw: float, duty_cycle: float, heater_type: str) -> float:
    # Source: core/building_thermal.py -> heater_air_demand
    """
    Стехиометрический воздух на горение (для справки/отображения).

    CH₄ + 2O₂ → CO₂ + 2H₂O
    1 м³ газа требует 9.5 м³ воздуха (2 м³ O₂ при 21% объёмных).

    Для расчёта Qmin используй heater_co2_dilution_m3h() — CO₂ dilution является
    определяющим ограничением и превышает combustion air в ~40 раз.
    """
    if heater_type != "gas_open":
        return 0.0

    gas_flow = (heater_power_kw / 100.0) * 10.0 * duty_cycle  # м³/ч газа (LHV ≈ 10 кВт/м³)
    return gas_flow * 9.5


def heater_co2_dilution_m3h(
    heater_power_kw: float,
    duty_cycle: float,
    heater_type: str,
    safety_factor: float = 1.5,
    limit_ppm: float = 3000.0,
    outdoor_ppm: float = 400.0,
) -> float:
    # Source: core/building_thermal.py -> heater_co2_dilution_m3h
    """
    Дополнительный Qmin для поддержания CO₂ < предела при работе горелок открытого горения.

    При открытом горении продукты сгорания (CO₂, H₂O) выходят в воздух помещения:
      CH₄ + 2O₂ → CO₂ + 2H₂O
      1 м³ природного газа (LHV ≈ 10 кВт/м³) → 1000 л CO₂

    Разбавление: ΔQmin = V_CO₂ [л/ч] / ΔCO₂ [л/м³] × safety
    ΔCO₂ = 2600 ppm = 2.6 л/м³  (лимит 3000 − фон 400 ppm) — значения исходника по
    умолчанию; параметры limit_ppm / outdoor_ppm добавлены при переносе, чтобы
    горелки разбавлялись до того же предела, что и дыхание свиней (РД-АПК — 2000 ppm).

    Это определяющий вентиляционный constraint, в ~40 раз превышающий combustion air.
    """
    if heater_type != "gas_open":
        return 0.0

    gas_flow_m3h = (heater_power_kw / 100.0) * 10.0 * duty_cycle  # м³ газа/ч
    co2_lh = gas_flow_m3h * 1000.0                                  # л CO₂/ч

    return (co2_lh / ((limit_ppm - outdoor_ppm) / 1000.0)) * safety_factor


def air_temperature_rise_c(num_animals: int, animal_weight_kg: float, t_air_c: float, airflow_m3s: float,
                           p_pa: float = P_ATM_PA) -> float:
    # Source: core/building_thermal.py -> air_temperature_rise_c (ADAPT: тепло свиней, p явно)
    """
    Нагрев воздуха по длине корпуса (от входа до вентиляторов), °C: явное тепло
    здания (CIGR 2002, pig_heat) / (ρ · Cp · Q). Свиньи у вентиляторов дышат этим,
    более тёплым воздухом — сравнивать с целью воздух на ВХОДЕ оптимистично.
    Явная доля берётся при T входа: она убывает с T, так что подъём чуть завышен
    (в безопасную сторону). Группа — растущие свиньи.
    """
    if num_animals <= 0 or animal_weight_kg <= 0 or airflow_m3s <= 0:
        return 0.0
    sensible_w = pig_heat("growing", animal_weight_kg, t_air_c).sensible_w * num_animals
    return sensible_w / (air_density_kg_m3(t_air_c, p_pa) * AIR_CP_J_PER_KG_K * airflow_m3s)


def ventilation_heat_w(q_m3h: float, t_inside_c: float, t_outside_c: float, p_pa: float = P_ATM_PA) -> float:
    # Source: core/building_thermal.py -> ventilation_heat_w (ADAPT: p явно)
    """
    Тепло, уносимое вентиляцией при нагреве приточного воздуха до T_in, Вт.

    Расход q — объём ВНУТРЕННЕГО воздуха: вытяжные вентиляторы перекачивают воздух
    помещения, их паспорт и уставки — в м³/ч при его температуре. Поэтому ρ берётся
    при T_in. Одна функция для Qmax, загрузки горелок и анализа теплового баланса.
    """
    dt = t_inside_c - t_outside_c
    return q_m3h / 3600.0 * air_density_kg_m3(t_inside_c, p_pa) * AIR_CP_J_PER_KG_K * dt


@dataclass
class HeaterDuty:
    """Согласованная загрузка горелок (см. solve_heater_duty)."""
    duty_cycle:   float   # 0…1 — доля времени работы
    qmin_m3h:     float   # минимальный воздухообмен ВМЕСТЕ с воздухом, который требуют сами горелки
    heat_need_w:  float   # потребность в тепле при этой загрузке, Вт
    insufficient: bool    # даже на полной мощности тепла не хватает
    full_load_need_w: float  # потребность при работе на 100 % — для дефицита в UI


def solve_heater_duty(
    qmin_for_duty: Callable[[float], float],
    ua_w_per_k: float,
    t_inside_c: float,
    t_outside_c: float,
    animals_sensible_w: float,
    heater_power_w: float,
    exchange_floor_m3h: float = 0.0,
    p_pa: float = P_ATM_PA,
) -> HeaterDuty:
    # Source: core/building_thermal.py -> solve_heater_duty (ADAPT: animals_sensible_w, p явно)
    """
    Загрузка горелок, согласованная с воздухом, который они сами требуют.

    Открытое пламя выделяет CO₂ и пар: на каждый кВт горелки нужно ~58 м³/ч
    воздуха на разбавление CO₂, и этот воздух тоже надо нагреть. При ΔT 30 К
    его подогрев съедает ~58 % мощности самой горелки. Если считать загрузку по
    Qmin БЕЗ этого воздуха, она занижается в 2–2.5 раза (аудит MINMAXapp 2026-10-18).

    Уравнение: d = потребность(d) / P, где
        потребность(d) = UA·ΔT + ρ·Cp·ΔT·Qmin(d)/3600 − тепло животных.
    qmin_for_duty(d) — Qmin при загрузке d (у открытого пламени растёт с d,
    у электрических не зависит). Решаем бисекцией: f(d) = потребность(d)/P − d,
    f(0) ≥ 0; если f(1) ≥ 0 — мощности не хватает и горелки работают на 100 %.

    exchange_floor_m3h — утечка при ~4 Па (ветер, тяга): греть приходится не меньше
    этого воздуха, даже когда Qmin меньше. К Qmin она не прибавляется — под
    разрежением щели лишь ещё один впуск.
    """
    def need_w(d: float) -> float:
        # Source: core/building_thermal.py -> solve_heater_duty.need_w
        q = max(qmin_for_duty(d), exchange_floor_m3h)
        vent = ventilation_heat_w(q, t_inside_c, t_outside_c, p_pa) if t_inside_c > t_outside_c else 0.0
        walls = ua_w_per_k * max(t_inside_c - t_outside_c, 0.0)
        return max(0.0, walls + vent - animals_sensible_w)

    full_need = need_w(1.0)
    if heater_power_w <= 0:
        return HeaterDuty(0.0, qmin_for_duty(0.0), need_w(0.0), need_w(0.0) > 0, full_need)

    if full_need >= heater_power_w:
        return HeaterDuty(1.0, qmin_for_duty(1.0), full_need, full_need > heater_power_w, full_need)

    lo, hi = 0.0, 1.0            # f(lo) ≥ 0, f(hi) < 0
    for _ in range(60):          # 2⁻⁶⁰ — заведомо точнее любых входных данных
        mid = 0.5 * (lo + hi)
        if need_w(mid) / heater_power_w - mid >= 0.0:
            lo = mid
        else:
            hi = mid
    d = lo
    return HeaterDuty(d, qmin_for_duty(d), need_w(d), False, full_need)


@dataclass
class HeatBalance:
    """Результат расчёта полного теплового баланса свинарника."""
    # Потери тепла
    q_walls_kw:         float   # через ограждения: UA × ΔT
    q_ventilation_kw:   float   # с вентиляционным воздухом
    q_total_loss_kw:    float   # суммарные потери

    # Приходная часть (не от обогревателей)
    q_animals_kw:        float   # явное тепло свиней на уровне здания
    q_per_animal_w:      float   # тепло одной головы, Вт

    # Баланс
    q_heater_required_kw: float     # сколько нужно от обогревателей (≥ 0)
    heater_covers_need:   bool      # мощность обогревателей достаточна
    duty_cycle:           float     # коэффициент загрузки (0..1)
    duty_cycle_pct:       float     # %

    # Газовое сжигание (только для gas_open)
    combustion_air_m3h:   float     # стехиометрический воздух горения, м³/ч (справочно)
    dilution_air_m3h:     float     # воздух на разбавление CO₂ открытого пламени — ДОБАВЛЯЕТСЯ к Qmin

    # Диагностика
    animal_heat_fraction: float     # доля потерь, покрытая теплом свиней (может >1)
    # Оценка кодом (перевод на клиенте) и уровнем для цвета
    status_code:          str       # no_heating_needed | animals_sufficient | insufficient | near_limit | moderate | light
    status_level:         str       # ok | warn | danger
    heater_deficit_kw:    float     # нехватка мощности при insufficient, иначе 0


def animal_sensible_heat_w(animal_weight_kg: float, t_inside_c: float, group: str = "growing", **heat_kw) -> float:
    # Source: core/building_thermal.py -> bird_sensible_heat_w (ADAPT: тепло свиней)
    """Явное тепло одной головы на уровне здания при температуре T, Вт (CIGR 2002, pig_heat)."""
    if animal_weight_kg <= 0:
        return 0.0
    return pig_heat(group, animal_weight_kg, t_inside_c, **heat_kw).sensible_w


def calculate_heat_balance(
    ua_coefficient:   float,    # Коэффициент теплопотерь здания, Вт/°C
    q_min_m3h:        float,    # Минимальный воздухообмен, м³/ч
    t_inside_target:  float,    # Целевая температура внутри, °C
    t_outside:        float,    # Температура снаружи, °C
    num_animals:      int,      # Поголовье
    animal_weight_kg: float,    # Средняя живая масса, кг
    heater_power_kw:  float,    # Суммарная мощность обогревателей, кВт (0 = нет)
    heater_type:      str = "gas_open",  # "gas_open" или "electric"
    p_pa:             float = P_ATM_PA,
    animals_sensible_w: float | None = None,   # явное тепло всего поголовья, если считано снаружи
    co2_limit_ppm:    float = 3000.0,
    co2_outdoor_ppm:  float = 400.0,
) -> HeatBalance:
    # Source: core/building_thermal.py -> calculate_heat_balance (ADAPT: тепло свиней, p явно)
    """
    Полный тепловой баланс свинарника с учётом тепла животных.

    Уравнение баланса:
      Q_heater = Q_walls + Q_ventilation − Q_animals
                 (неотрицательно — обогреватели не охлаждают)

    animals_sensible_w — явное тепло поголовья, посчитанное вызывающим (другая
    группа, методика, поправка ks). Не задано — растущие свиньи по CIGR 2002.
    """
    dt = max(t_inside_target - t_outside, 0.0)   # разница температур

    # Тепло животных
    if animals_sensible_w is None:
        q_per_animal_w = animal_sensible_heat_w(animal_weight_kg, t_inside_target)
        q_animals_w = num_animals * q_per_animal_w
    else:
        q_animals_w = animals_sensible_w
        q_per_animal_w = animals_sensible_w / num_animals if num_animals > 0 else 0.0

    # q_min_m3h — минимум ЖИВОТНЫХ; открытое пламя добавляет свой воздух разбавления,
    # и его тоже нужно нагреть. Загрузку ищем согласованно (solve_heater_duty)
    def qmin_for_duty(d: float) -> float:
        # Source: core/building_thermal.py -> calculate_heat_balance.qmin_for_duty
        return q_min_m3h + heater_co2_dilution_m3h(heater_power_kw, d, heater_type,
                                                   limit_ppm=co2_limit_ppm, outdoor_ppm=co2_outdoor_ppm)

    sol = solve_heater_duty(qmin_for_duty, ua_coefficient, t_inside_target, t_outside,
                            q_animals_w, heater_power_kw * 1000.0, p_pa=p_pa)
    duty_cycle     = sol.duty_cycle
    dilution_air   = qmin_for_duty(duty_cycle) - q_min_m3h

    # Потери через ограждения и с вентиляционным воздухом (вместе с воздухом горелок)
    q_walls_kw = ua_coefficient * dt / 1000.0
    q_vent_kw  = (ventilation_heat_w(sol.qmin_m3h, t_inside_target, t_outside, p_pa) / 1000.0
                  if dt > 0 else 0.0)
    q_total_loss_kw = q_walls_kw + q_vent_kw
    q_animals_kw = q_animals_w / 1000.0

    # Потребность: при найденной загрузке; если мощности не хватает — при работе на 100 %
    q_need_kw = sol.heat_need_w / 1000.0
    animal_fraction = q_animals_kw / q_total_loss_kw if q_total_loss_kw > 0 else 0.0

    covers = not sol.insufficient
    combustion_air = heater_air_demand(heater_power_kw, duty_cycle, heater_type)

    # Оценка: код + уровень, текст собирает клиент на своём языке
    deficit = 0.0
    if dt <= 0:
        status, level = "no_heating_needed", "ok"
    elif q_need_kw == 0:
        status, level = "animals_sufficient", "ok"
    elif not covers:
        deficit = sol.full_load_need_w / 1000.0 - heater_power_kw
        status, level = "insufficient", "danger"
    elif duty_cycle >= 0.9:
        status, level = "near_limit", "warn"
    elif duty_cycle >= 0.5:
        status, level = "moderate", "ok"
    else:
        status, level = "light", "ok"

    return HeatBalance(
        q_walls_kw            = round(q_walls_kw, 2),
        q_ventilation_kw      = round(q_vent_kw, 2),
        q_total_loss_kw       = round(q_total_loss_kw, 2),
        q_animals_kw          = round(q_animals_kw, 2),
        q_per_animal_w        = round(q_per_animal_w, 2),
        q_heater_required_kw  = round(q_need_kw, 2),
        heater_covers_need    = covers,
        duty_cycle            = round(duty_cycle, 4),
        duty_cycle_pct        = round(duty_cycle * 100, 1),
        combustion_air_m3h    = round(combustion_air, 1),
        dilution_air_m3h      = round(dilution_air, 1),
        animal_heat_fraction  = round(animal_fraction, 4),
        status_code           = status,
        status_level          = level,
        heater_deficit_kw     = round(deficit, 2),
    )
