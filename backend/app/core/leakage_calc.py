"""
Перенесено из MINMAXapp (backend/app/core/leakage_calc.py, 59390c7) по переписи
docs/porting/house_census.md.

Калькулятор герметичности (воздухопроницаемости) корпуса. Методика UGA выведена
для птичников; физика щелей (Бернулли, степенной закон) от вида животного не зависит.

Основан на методике UGA (University of Georgia, Michael Czarick):
  https://www.poultryventilation.com/resources/poultry-house-leakage-area-calculator-2018/

Физика:
  ELA (Equivalent Leakage Area) рассчитывается из уравнения Бернулли:
    Q = ELA * sqrt(2 * ΔP / ρ)
  Откуда:
    ELA = Q_m3s * sqrt(ρ / (2 * ΔP_Pa))

  Коэффициент расхода Cd здесь сознательно = 1, и ρ = 1.184 — константа: ровно так
  считает калькулятор UGA (poultry_house_air_leakage_calculator_2018.xlsx, ячейка
  L2: =I2*(J2/(2*K2))^0.5, J2 = 1.184), и его пороги классов привязаны к этой
  величине. С Cd = 0.61 (ELA по ASHRAE/LBL) площадь вышла бы в 1/0.61 раза больше
  и классы UGA к ней уже не применимы. Это условная площадь, а не площадь щелей.

  где Q_m3s — расход вентилятора при измеренном давлении (м³/с),
  ρ = 1.184 кг/м³ — плотность воздуха при +20°C,
  ΔP_Pa — измеренное статическое давление, Па.

Поправка производительности вентилятора на давление теста:
  Вентилятор, указанный при 0.10" (25 Па), при тестовом давлении P_in (в дюймах):
    Q_corrected = Q_rated * (-2.57 * P_in² - 0.97 * P_in + 1.124)
  Это эмпирический полином UGA, откалиброванный на типичных туннельных вентиляторах.
"""

from __future__ import annotations
import math
from dataclasses import dataclass

from .building_thermal import AIR_CP_J_PER_KG_K, air_density_kg_m3
from .errors import CalcError
from typing import Literal, Optional

# Физические константы
RHO_AIR_KG_M3 = 1.184          # Плотность воздуха при +20°C, кг/м³
IN_H2O_TO_PA  = 249.089         # 1 дюйм водяного столба = 249.089 Па (точное значение SI)

# Правило UGA: 750 CFM на ft² открытой площади впуска → в метрике:
# 750 CFM/ft² = 750 * 1.699 м³ч / (0.0929 м²) = 13 717 м³ч/м²
INLET_SPECIFIC_M3H_M2 = 13_717.0


TightnessClass = Literal["tight", "moderate", "leaky", "very_leaky"]


@dataclass
class LeakageInput:
    # Размеры корпуса
    house_length_m: float          # Длина, м
    house_width_m:  float          # Ширина, м

    # Параметры теста герметичности
    fan_capacity_m3h: float        # Производительность тестового вентилятора при 25 Па (≈0.10"), м³/ч
    measured_pressure_pa: float    # Статическое давление при тесте, Па (рекомендация: 25-65 Па)
    # Дополнительные точки теста (другое число вентиляторов): [(расход при 25 Па, давление)].
    # С ними — степенной закон Q = C·ΔPⁿ вместо одной точки с n = 0.5 (Бернулли)
    extra_points: tuple = ()


@dataclass
class LeakageResult:
    fan_corrected_m3h: float       # Скорректированный расход вентилятора при тестовом давлении, м³/ч
    ela_m2: float                  # Суммарная площадь щелей/утечек (ELA), м²
    house_area_m2: float           # Площадь пола корпуса, м²
    relative_leakage: float        # Относительная утечка, м² на 1000 м² пола (безразмерна)
    tightness_class: TightnessClass  # Класс герметичности — текст подставляет клиент (tightness_<class>)
    static_pressure_pa: float      # Введённое давление (для справки)
    # Многоточечный тест (None — одна точка): Q = C·ΔPⁿ
    flow_exponent_n: Optional[float] = None       # 0.5 — крупные отверстия, 1.0 — узкие щели
    flow_coefficient: Optional[float] = None      # C, м³/ч при 1 Па
    leak_at_4pa_m3h: Optional[float] = None       # естественная инфильтрация (≈ 4 Па: ветер, тяга)
    infiltration_kw_per_10k: Optional[float] = None   # её теплопотери на каждые 10 °C разницы, кВт
    exponent_out_of_range: bool = False           # n вне 0.5–1.0 — точки теста противоречивы


# Классификация по относительной утечке (м²/1000 м² = ft²/1000 ft² — безразмерна).
#
# Пороги UGA (Czarick, Fairchild), сверено с первоисточниками 2026-10-19:
#   0.48 и 0.65 — «Poultry House Leakage Estimating Spreadsheet 2014»: классы
#     < 0.48 / 0.48–0.65 / 0.65–1.2 / > 1.2, и у каждого физический смысл — доля
#     воздуха минвентиляции, входящая через клапаны, а не щели: > 70 % / 50–70 % /
#     12–50 % / меньше. Цель UGA — не меньше 50 %, то есть граница 0.65.
#   1.0 — «Poultry411 App — Poultry House Tightness Calculator» (2018, новее):
#     «above 1 is viewed as very poor»; там же 0.45 названо excellent, 0.78 — marginal,
#     что ложится в эти классы.
# Прежние 0.25 / 0.50 / 1.00 источника не имели: дом с 0.35 («excellent» у UGA)
# получал «умеренно».
_TIGHTNESS_THRESHOLDS: list[tuple[float, TightnessClass]] = [
    (0.48, "tight"),
    (0.65, "moderate"),
    (1.00, "leaky"),
]


def _classify(relative_leakage: float) -> TightnessClass:
    # Source: core/leakage_calc.py -> _classify
    for threshold, cls in _TIGHTNESS_THRESHOLDS:
        if relative_leakage < threshold:
            return cls
    return "very_leaky"


def _fan_correction(q_rated_m3h: float, pressure_pa: float) -> float:
    # Source: core/leakage_calc.py -> _fan_correction
    """
    Поправка производительности вентилятора на фактическое давление.

    Вентиляторы калиброваны при 25 Па (≈ 0.10"). При более высоком давлении
    производительность падает нелинейно. Полином UGA (P_in в дюймах вод. ст.):
        multiplier = -2.57 * P_in² - 0.97 * P_in + 1.124
    """
    p_in = pressure_pa / IN_H2O_TO_PA          # Па → дюймы
    multiplier = -2.57 * p_in**2 - 0.97 * p_in + 1.124
    multiplier = max(0.0, multiplier)            # Не может быть отрицательным
    return q_rated_m3h * multiplier


def calculate_leakage(inp: LeakageInput) -> LeakageResult:
    # Source: core/leakage_calc.py -> calculate_leakage
    """Расчёт герметичности корпуса."""

    # Площадь пола
    house_area_m2 = inp.house_length_m * inp.house_width_m

    # Скорректированный расход вентилятора при давлении теста
    q_corrected_m3h = _fan_correction(inp.fan_capacity_m3h, inp.measured_pressure_pa)
    q_corrected_m3s = q_corrected_m3h / 3600.0

    # ELA: уравнение Бернулли обращённое
    # Q = ELA * sqrt(2*P/rho) → ELA = Q * sqrt(rho/(2*P))
    ela_m2 = q_corrected_m3s * math.sqrt(RHO_AIR_KG_M3 / (2.0 * inp.measured_pressure_pa))

    # Относительная утечка (безразмерная — одинакова в имперской и метрической системах)
    relative_leakage = ela_m2 / (house_area_m2 / 1000.0)

    # Класс герметичности
    t_class = _classify(relative_leakage)

    # Многоточечный тест: ln Q = ln C + n·ln ΔP по МНК (методика LBNL / ASTM E779 —
    # метод открытый). n физически между 0.5 (отверстия, турбулентно) и 1.0 (узкие
    # щели, ламинарно); одна точка молча предполагает 0.5 и переоценивает утечку при
    # малых перепадах, где корпус живёт без вентиляторов (ветер, тяга — ~4 Па)
    power_law = {}
    if inp.extra_points:
        pts = [(q_corrected_m3h, inp.measured_pressure_pa)] + [
            (_fan_correction(q, dp), dp) for q, dp in inp.extra_points]
        xs = [math.log(dp) for _, dp in pts]
        ys = [math.log(q) for q, _ in pts]
        if max(xs) - min(xs) < math.log(1.3):
            # Давления почти равны — наклон (n) из них не определить
            raise CalcError("leakage_points_too_close")
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        n = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
        c = math.exp(my - n * mx)
        q4 = c * 4.0 ** n
        power_law = dict(
            flow_exponent_n         = round(n, 3),
            flow_coefficient        = round(c, 1),
            leak_at_4pa_m3h         = round(q4, 0),
            infiltration_kw_per_10k = round(q4 / 3600.0 * air_density_kg_m3(0.0) * AIR_CP_J_PER_KG_K * 10.0 / 1000.0, 1),
            exponent_out_of_range   = not (0.5 <= n <= 1.0),
        )

    return LeakageResult(
        **power_law,
        fan_corrected_m3h  = round(q_corrected_m3h, 0),
        ela_m2             = round(ela_m2, 4),
        house_area_m2      = round(house_area_m2, 1),
        relative_leakage   = round(relative_leakage, 4),
        tightness_class    = t_class,
        static_pressure_pa = inp.measured_pressure_pa,
    )
