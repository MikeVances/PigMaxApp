"""
Перенесено из MINMAXapp (backend/app/core/evap_cooling_calc.py, 59390c7) по переписи
docs/porting/house_census.md. Мосты (ADAPT): поголовье и масса свиней вместо птицы
(нагрев воздуха по длине — тепло свиней, pig_heat); давление площадки — явным
полем p_pa вместо настроек хозяйства.

Калькулятор системы испарительного охлаждения свинарника (Pad & Fan).

Методика UGA (University of Georgia, Michael Czarick):
  evaporative_cooling_pad_system_design_2014_v2.xlsx
  evaporative_cooling_design_spreadsheet_2014_-_metric.xlsx

ДАННЫЕ ПАНЕЛЕЙ: таблицы SANHE (оцифровано с кривых производителя):
─────────────────────────────────────────────────────────────────
  Эффективность насыщения панели (saturation efficiency):
    η = (T_db_in − T_db_out) / (T_db_in − T_wb_in) × 100%
    Интерполируется линейно по скорости воздуха через панель.
    — не КПД по энергии, а степень приближения к T_wb.

  Типы панелей SANHE:
    7090 — высокоэффективный (угол гофры 70°/90°): высокая η, больший ΔP
    7060 — стандартный (70°/60°): ниже ΔP, ниже η при той же толщине
    5090 — компактный (50°/90°): высокая η для своей толщины, тонкие секции

  ВАЖНО: η снижается с ростом скорости (меньше времени контакта с влажной поверхностью).
  Значения в таблицах — точки на кривой η(v) производителя. Выше 2.5 м/с риск уноса капель.

Водопотребление (ПСИХРОМЕТРИЧЕСКИЙ МАССОВЫЙ БАЛАНС, как в UGA):
  p_ws(T) = 611.2 × exp(17.67T / (T+243.5))  (Magnus-Tetens, Па)
  ΔW = η × (W_sat(T_wb) − W_outside)          [кг воды / кг сухого воздуха]
  Q_water = (Q_air/3600) × m_da × ΔW × 60     [л/мин]
  m_da — масса сухого воздуха в кубометре НА ВЫХОДЕ панели: вентиляторы качают
  уже охлаждённый увлажнённый воздух, и их паспортный расход — объём этого воздуха.

  Сверка: карта расхода воды UGA (Czarick) — 90 °F, 60 % RH, 100 000 cfm, панель 6"
  → ≈ 1.75 gpm; здесь при КПД 0.75 (FSA-1068 для панели 6") — 1.76 gpm
  (test_psychro.py). Прежний расчёт (Стулл + ρ = 1.184) давал 2.03 gpm, +16 %.

Психрометрия — core/psychro.py (ASHRAE Fundamentals 2017): мокрый термометр
итерацией, а не формулой Стулла, — та в сухую жару ошибалась до +1 °C.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .building_thermal import air_temperature_rise_c
from .psychro import P_ATM_PA, dew_point_c, dry_air_kg_per_m3, humidity_ratio, moist_air_density, wet_bulb_c

# Проектная скорость через панель для расчёта рекомендуемой площади, м/с, по толщине
# (UGA, Czarick: 350 fpm ≈ 1.78 м/с для 150 мм и 225 fpm ≈ 1.14 м/с для 100 мм —
# https://thepoultrysite.com/articles/evaporative-cooling-pad-design-spreadsheet).
# Раньше одна скорость 1.25 м/с для всех толщин: для 150 мм площадь выходила
# в 1.4 раза больше нужной (аудит 2026-10-18). Между опорами — линейно,
# за их пределами — ближайшая опора (других данных у UGA нет).
_V_DESIGN_BY_THICKNESS = [(100, 1.14), (150, 1.78)]


def design_pad_velocity_ms(thickness_mm: float) -> float:
    # Source: core/evap_cooling_calc.py -> design_pad_velocity_ms
    """Проектная скорость воздуха через панель данной толщины, м/с."""
    (t0, v0), (t1, v1) = _V_DESIGN_BY_THICKNESS
    if thickness_mm <= t0:
        return v0
    if thickness_mm >= t1:
        return v1
    return v0 + (v1 - v0) * (thickness_mm - t0) / (t1 - t0)


# ─────────────────────────────────────────────────────────────────
# Данные панелей SANHE (оцифровано с кривых производителя)
# structure: velocities [м/с], thicknesses [мм],
#   efficiency_pct [%] и pressure_pa [Па] — как функция скорости для каждой толщины
# ─────────────────────────────────────────────────────────────────
SANHE_PADS: dict[str, dict] = {
    "7090": {
        "description": "SANHE 7090 — высокоэффективный (гофра 70°/90°)",
        "velocities":  [0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
        "thicknesses": [75, 100, 150, 200, 300],
        "v_min_ms":    0.6,
        "v_max_ms":    2.5,
        "efficiency_pct": {
            75:  [73, 68, 64, 60, 57, 55],
            100: [83, 77, 72, 69, 67, 66],
            150: [93, 89, 86, 83, 82, 81],
            200: [97, 95, 92, 90, 89, 89],
            300: [99, 98, 97, 97, 96, 96],
        },
        "pressure_pa": {
            75:  [5,  8,  14, 22,  33,  45],
            100: [7,  12, 20, 31,  45,  60],
            150: [10, 18, 32, 48,  68,  90],
            200: [15, 28, 48, 72,  105, 145],
            300: [25, 45, 75, 115, 165, 215],
        },
    },
    "7060": {
        "description": "SANHE 7060 — стандартный (гофра 70°/60°)",
        "velocities":  [0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
        "thicknesses": [75, 100, 150, 200, 300],
        "v_min_ms":    0.6,
        "v_max_ms":    2.5,
        "efficiency_pct": {
            75:  [66, 58, 53, 50, 48, 46],
            100: [75, 69, 63, 59, 56, 54],
            150: [88, 82, 76, 72, 69, 67],
            200: [95, 90, 85, 82, 80, 78],
            300: [97, 95, 94, 93, 91, 90],
        },
        "pressure_pa": {
            75:  [2,  5,  8,  13, 18, 25],
            100: [4,  7,  12, 18, 26, 35],
            150: [6,  10, 17, 26, 36, 48],
            200: [8,  14, 24, 36, 50, 65],
            300: [12, 22, 37, 55, 75, 90],
        },
    },
    "5090": {
        "description": "SANHE 5090 — компактный (гофра 50°/90°)",
        "velocities":  [0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
        "thicknesses": [50, 75, 100, 150],
        "v_min_ms":    0.6,
        "v_max_ms":    2.5,
        "efficiency_pct": {
            50:  [74, 69, 65, 62, 60, 59],
            75:  [86, 82, 78, 74, 72, 71],
            100: [93, 90, 87, 85, 83, 82],
            150: [98, 96, 94, 92, 91, 91],
        },
        "pressure_pa": {
            50:  [5,  10, 16, 24,  34,  45],
            75:  [8,  15, 25, 38,  55,  72],
            100: [12, 23, 37, 55,  78,  98],
            150: [20, 35, 55, 80, 115, 150],
        },
    },
}

PAD_TYPE_DEFAULT = "7090"


# ─────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────

@dataclass
class EvapCoolingInput:
    # Размеры свинарника (для контекста/аудита)
    house_length_m: float          # Длина свинарника, м

    # Климатические условия
    t_outdoor_db_c: float          # Температура сухого термометра снаружи, °C
    rh_outdoor_pct: float          # Относительная влажность снаружи, %

    # Система вентиляции
    total_fan_m3h: float           # Суммарная производительность туннельных вентиляторов, м³/ч

    # Параметры панели
    pad_type: str                  # Тип панели SANHE: "7090", "7060", "5090"
    pad_thickness_mm: int          # Толщина панели, мм (зависит от типа)
    pad_length_m: float            # Суммарная длина панелей охлаждения, м
    pad_height_m: float            # Высота панелей охлаждения, м

    # Целевая температура внутри (для оценки эффективности)
    t_inside_target_c: float       # Целевая температура внутри свинарника, °C

    # Поголовье и масса — для нагрева воздуха от панелей до вентиляторов (0 = не учитывать)
    num_animals: int = 0
    animal_weight_kg: float = 0.0

    # Суммарный расход вентиляторов при 25 Па (вторая точка паспорта), м³/ч; 0 = неизвестен.
    # С ним расход считается в рабочей точке — с сопротивлением самой панели
    total_fan_25pa_m3h: float = 0.0

    # Опционально: температура воды подачи (скважина / бак)
    # None = стандартный адиабатический расчёт (как в UGA)
    water_supply_t_c: Optional[float] = None
    # КПД насыщения, заданный вручную, % (None — по паспорту SANHE, т. е. новая панель).
    # Паспорт оптимистичнее работы в корпусе: UGA — 70–75 % в эксплуатации, FSA-1068
    # (Univ. of Arkansas) — 75 % для панели 150 мм; коэффициента «паспорт → поле»
    # ни один источник не даёт, поэтому не пересчитываем, а даём задать своё число
    pad_efficiency_pct: Optional[float] = None
    # Давление площадки, Па (по высоте) — мост вместо site_pressure_pa() исходника
    p_pa: float = P_ATM_PA


@dataclass
class EvapCoolingResult:
    # --- Психрометрика ---
    t_wb_c: float                  # Температура мокрого термометра, °C
    t_pad_out_c: float             # Температура воздуха после панели, °C
    cooling_achieved_c: float      # Достигнутое охлаждение, °C
    pad_efficiency: float          # Эффективность насыщения η (0..1): η=(T_db_in−T_db_out)/(T_db_in−T_wb_in)

    # --- Геометрия панели ---
    pad_area_m2: float             # Фактическая площадь панелей, м²
    air_velocity_ms: float         # Скорость воздуха через панель, м/с
    velocity_ok: bool              # Скорость в допустимом диапазоне
    velocity_status: str           # too_low | ok | too_high (перевод на клиенте)
    pad_v_min_ms: float            # Допустимый диапазон скорости для этой панели —
    pad_v_max_ms: float            # клиенту для текста статуса
    pad_pressure_drop_pa: float    # Перепад давления на панели, Па (SANHE, интерп. по скорости)

    # --- Водопотребление ---
    water_flow_lpm: float          # Расход воды, л/мин
    water_per_hour_l: float        # Водопотребление, л/ч

    # --- Оценка условий в свинарнике ---
    cooling_sufficient: bool       # Охлаждение достаточное (T_pad < T_target)
    temp_margin_c: float           # Запас температуры (T_target − T у вентиляторов), °C

    # --- Рекомендации ---
    recommended_pad_area_m2: float  # Рекомендуемая площадь панели для проектной скорости толщины
    status_code: str               # pad_too_small | pad_too_large | cooling_insufficient | cooling_marginal | ok
    status_level: str              # ok | warn | danger — цвет бейджа на клиенте

    # --- Точка росы и проверка температуры воды подачи ---
    t_dew_c: float                         # Точка росы входящего воздуха, °C (всегда вычисляется)
    water_supply_t_c: Optional[float]      # Температура воды подачи (None если не задана)
    # 'safe'             — T_вода > T_dp, испарение не нарушено
    # 'condensation_risk'— T_вода < T_dp, конденсация на панели, потеря эффективности насыщения
    # None               — температура воды не указана
    water_regime: Optional[str]
    air_temp_rise_c: float = 0.0   # нагрев воздуха от панелей до вентиляторов, °C
    t_fan_end_c: float = 0.0       # T воздуха у вентиляторов — по ней оценка достаточности
    airflow_m3h: float = 0.0       # расход, по которому считаны скорость, КПД и вода
    airflow_basis: str = "rated_0pa"   # operating_point (с сопротивлением панели) | rated_0pa
    efficiency_basis: str = "passport"     # "passport" (SANHE, новая панель) | "user" (задан вручную)


# ─────────────────────────────────────────────────
# Вспомогательные функции
# ─────────────────────────────────────────────────

def _interp_linear(x_vals: list[float], y_vals: list[float], x: float) -> float:
    # Source: core/evap_cooling_calc.py -> _interp_linear
    """
    Линейная интерполяция с зажимом к граничным значениям таблицы.

    Не экстраполирует: за пределами диапазона возвращает крайнее значение.
    Подходит для кривых η(v) и ΔP(v), которые монотонны и не имеют
    физически обоснованного продолжения за границы измерений.
    """
    if x <= x_vals[0]:
        return y_vals[0]
    if x >= x_vals[-1]:
        return y_vals[-1]
    for i in range(len(x_vals) - 1):
        if x_vals[i] <= x <= x_vals[i + 1]:
            t = (x - x_vals[i]) / (x_vals[i + 1] - x_vals[i])
            return y_vals[i] + t * (y_vals[i + 1] - y_vals[i])
    return y_vals[-1]


def _water_flow_lpm(
    fan_m3h: float,
    t_db: float,
    rh_pct: float,
    t_wb: float,
    efficiency: float,
    p_pa: float = P_ATM_PA,
) -> float:
    # Source: core/evap_cooling_calc.py -> _water_flow_lpm
    """
    Расход воды — психрометрический массовый баланс (метод UGA Excel).

    Воздух, проходя через панель с эффективностью насыщения η, достигает промежуточного состояния
    на линии адиабатического насыщения между исходной точкой (T_db, W_outside)
    и точкой насыщения (T_wb, W_sat(T_wb)):

      ΔW = η × (W_sat(T_wb) − W_outside)   [кг воды / кг сух. воздуха]
      Q_water = (Q / 3600) × ρ × ΔW × 60   [л/мин]

    """
    w_outside = humidity_ratio(t_db, rh_pct, p_pa)
    w_sat_wb  = humidity_ratio(t_wb, 100.0, p_pa)   # влагосодержание при T_wb, RH=100%

    dw = efficiency * (w_sat_wb - w_outside)
    if dw <= 0:
        return 0.0

    # Паспортный расход вентиляторов — объём воздуха после панели (они стоят за ней)
    t_out = t_db - efficiency * (t_db - t_wb)
    q_m3s = fan_m3h / 3600.0
    # Масса испарённой воды = масса сухого воздуха × ΔW
    m_water_kg_s = q_m3s * dry_air_kg_per_m3(t_out, w_outside + dw, p_pa) * dw
    # Перевод в л/мин (плотность воды ≈ 1 кг/л)
    return m_water_kg_s * 60.0


FAN_RATING_POINT_PA = 25.0   # вторая точка паспорта вентилятора (≈ 0.10" вод. ст.)


def _pad_pressure_pa(velocities: list[float], dp_table: list[float], v: float) -> float:
    # Source: core/evap_cooling_calc.py -> _pad_pressure_pa
    """
    Сопротивление панели при скорости v, Па. Ниже первой точки таблицы — линейно к нулю
    (зажим к dp(0.5 м/с) давал бы сопротивление и у неподвижного воздуха).
    """
    if v < velocities[0]:
        return dp_table[0] * v / velocities[0]
    return _interp_linear(velocities, dp_table, v)


def pad_operating_airflow_m3s(q0_m3s: float, q25_m3s: float, pad_area_m2: float,
                              velocities: list[float], dp_table: list[float]) -> float:
    # Source: core/evap_cooling_calc.py -> pad_operating_airflow_m3s
    """
    Расход системы вентиляторов в рабочей точке с сопротивлением панели, м³/с.

    Характеристика (линейно по двум точкам паспорта): Q = Q₀ − s·P, s = (Q₀ − Q₂₅)/25 Па.
    Сопротивление: ΔP панели по кривой производителя при v = Q / A. Раньше брался
    паспортный расход при 0 Па, хотя панель 150 мм при 1.8 м/с сама даёт ~40 Па —
    скорость, КПД и расход воды были завышены (аудит 2026-10-18).
    Бисекция: g(Q) = Q₀ − s·ΔP(Q/A) − Q убывает, g(0) = Q₀ > 0.
    """
    s_slope = (q0_m3s - q25_m3s) / FAN_RATING_POINT_PA
    lo, hi = 0.0, q0_m3s
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if q0_m3s - s_slope * _pad_pressure_pa(velocities, dp_table, mid / pad_area_m2) - mid > 0:
            lo = mid
        else:
            hi = mid
    return lo


def calculate_evap_cooling(inp: EvapCoolingInput) -> EvapCoolingResult:
    # Source: core/evap_cooling_calc.py -> calculate_evap_cooling
    """Расчёт системы испарительного охлаждения свинарника."""

    # Получаем данные панели из таблицы SANHE
    pad_data  = SANHE_PADS.get(inp.pad_type, SANHE_PADS[PAD_TYPE_DEFAULT])
    velocities = pad_data["velocities"]
    eta_table  = pad_data["efficiency_pct"][inp.pad_thickness_mm]
    dp_table   = pad_data["pressure_pa"][inp.pad_thickness_mm]
    v_min      = pad_data["v_min_ms"]
    v_max      = pad_data["v_max_ms"]

    # Температура мокрого термометра
    t_wb = wet_bulb_c(inp.t_outdoor_db_c, inp.rh_outdoor_pct, inp.p_pa)

    # Геометрия панели — прямые размеры: длина × высота
    pad_area_m2 = inp.pad_length_m * inp.pad_height_m
    if pad_area_m2 <= 0:
        pad_area_m2 = 0.001  # защита от деления на ноль

    # Скорость воздуха через панель: Q / A
    q0_m3s = inp.total_fan_m3h / 3600.0
    q25_m3s = inp.total_fan_25pa_m3h / 3600.0
    if 0.0 < q25_m3s < q0_m3s:
        q_m3s = pad_operating_airflow_m3s(q0_m3s, q25_m3s, pad_area_m2, velocities, dp_table)
        airflow_basis = "operating_point"
    else:
        q_m3s = q0_m3s
        airflow_basis = "rated_0pa"      # завышен: паспорт без сопротивления панели
    q_m3h = q_m3s * 3600.0
    air_velocity_ms = q_m3s / pad_area_m2

    # Интерполяция η и ΔP по фактической скорости
    eta_pct = _interp_linear(velocities, eta_table, air_velocity_ms)
    # Таблица SANHE — на стандартном воздухе (1.2 кг/м³); фактическое сопротивление ∝ ρ
    dp_pa   = _interp_linear(velocities, dp_table,  air_velocity_ms) * moist_air_density(inp.t_outdoor_db_c, inp.rh_outdoor_pct, inp.p_pa) / 1.2
    efficiency_basis = "passport"
    if inp.pad_efficiency_pct is not None:
        eta_pct, efficiency_basis = inp.pad_efficiency_pct, "user"
    eta     = eta_pct / 100.0

    # Температура воздуха после панели
    t_pad_out = inp.t_outdoor_db_c - eta * (inp.t_outdoor_db_c - t_wb)
    cooling_achieved = inp.t_outdoor_db_c - t_pad_out

    # Проверка диапазона скорости
    velocity_ok = v_min <= air_velocity_ms <= v_max
    if air_velocity_ms < v_min:
        velocity_status = "too_low"      # риск плесени на панели
    elif air_velocity_ms > v_max:
        velocity_status = "too_high"     # брызги воды в свинарник
    else:
        velocity_status = "ok"

    # Водопотребление (психрометрический массовый баланс)
    water_lpm = _water_flow_lpm(q_m3h, inp.t_outdoor_db_c, inp.rh_outdoor_pct, t_wb, eta, inp.p_pa)
    water_per_hour = water_lpm * 60.0

    # Оценка эффективности — у вентиляторов: охлаждённый воздух по пути нагревается
    # теплом свиней, и сравнивать с целью воздух сразу за панелью оптимистично
    air_rise = air_temperature_rise_c(inp.num_animals, inp.animal_weight_kg, t_pad_out, q_m3s, inp.p_pa)
    t_fan_end = t_pad_out + air_rise
    temp_margin = inp.t_inside_target_c - t_fan_end
    cooling_sufficient = t_fan_end <= inp.t_inside_target_c

    # Рекомендуемая площадь панели — для проектной скорости этой толщины
    recommended_pad_area = q_m3s / design_pad_velocity_ms(inp.pad_thickness_mm)

    # Общая оценка
    if not velocity_ok and air_velocity_ms > v_max:
        status_code, status_level = "pad_too_small", "danger"
    elif not velocity_ok and air_velocity_ms < v_min:
        status_code, status_level = "pad_too_large", "danger"
    elif not cooling_sufficient:
        status_code, status_level = "cooling_insufficient", "danger"
    elif temp_margin < 2.0:
        status_code, status_level = "cooling_marginal", "warn"
    else:
        status_code, status_level = "ok", "ok"

    # Точка росы входящего воздуха (всегда). Вода холоднее неё — пар конденсируется
    # на панели, выделяя скрытую теплоту, и КПД насыщения падает
    t_dew = dew_point_c(inp.t_outdoor_db_c, inp.rh_outdoor_pct)

    # Режим подачи воды (если задана температура)
    water_regime: Optional[str] = None
    if inp.water_supply_t_c is not None:
        # Добавляем 0.5°C запаса: панель чуть теплее чем вода подачи из-за теплообмена
        water_regime = 'safe' if inp.water_supply_t_c >= (t_dew + 0.5) else 'condensation_risk'

    return EvapCoolingResult(
        t_wb_c                  = round(t_wb, 1),
        t_pad_out_c             = round(t_pad_out, 1),
        cooling_achieved_c      = round(cooling_achieved, 1),
        pad_efficiency          = round(eta, 4),
        pad_area_m2             = round(pad_area_m2, 2),
        air_velocity_ms         = round(air_velocity_ms, 2),
        velocity_ok             = velocity_ok,
        velocity_status         = velocity_status,
        pad_v_min_ms            = v_min,
        pad_v_max_ms            = v_max,
        pad_pressure_drop_pa    = round(dp_pa, 1),
        water_flow_lpm          = round(water_lpm, 1),
        water_per_hour_l        = round(water_per_hour, 0),
        cooling_sufficient      = cooling_sufficient,
        temp_margin_c           = round(temp_margin, 1),
        air_temp_rise_c         = round(air_rise, 1),
        t_fan_end_c             = round(t_fan_end, 1),
        airflow_m3h             = round(q_m3h, 0),
        airflow_basis           = airflow_basis,
        recommended_pad_area_m2 = round(recommended_pad_area, 2),
        status_code             = status_code,
        status_level            = status_level,
        t_dew_c                 = round(t_dew, 1),
        water_supply_t_c        = inp.water_supply_t_c,
        efficiency_basis        = efficiency_basis,
        water_regime            = water_regime,
    )
