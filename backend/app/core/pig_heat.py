"""
Тепло, влага и CO₂ свиней всех технологических групп — одна модель для всех балансов.

До 2026-10 калькулятор считал тепло как 5.09·W^0.75 — это только ПОДДЕРЖАНИЕ
из CIGR 2002 (первое слагаемое ур. 8), без тепла от переработки корма. Для
откорма 100 кг выходило 161 Вт вместо 215–300 Вт по всем источникам
(docs/sources.md, 1.4), и это полное тепло целиком шло в обогрев, хотя воздух
греет только явная часть.

Три методики — параметр запроса, в ответе всегда видно, какая взята (как выбор
методики кросса в MINMAXapp). Разброс между ними — не ошибка, а разные эпохи
генетики и уровни кормления:

  cigr2002          — база: CIGR 2002, все группы (sources: cigr_2002)
  brown_brandl_2004 — современная генетика США, только растущие (brown_brandl_2004)
  rd_apk_2012       — российская норма проектирования, все группы (rd_apk_2012)
  stinn_xin_2014    — измерения современного комплекса США (калориметрия по O₂/CO₂),
                      свиноматки: супоросные и подсосные с гнездом по дню лактации

Схема та же, что у птицы в MINMAXapp (bird_heat.py): полное тепло → явное на
уровне здания → скрытое = полное − явное → пар = скрытое / теплота парообразования.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Literal, Optional

# ─── Типы ─────────────────────────────────────────────────────────────────────

# Технологические группы. «growing» — весь растущий молодняк от отъёма до убоя
# и ремонтные свинки до осеменения: у CIGR и РД-АПК он различается только
# массой, а не отдельной формулой
Group = Literal["growing", "boar", "dry_sow", "gestating_sow", "lactating_sow"]
Method = Literal["cigr2002", "brown_brandl_2004", "rd_apk_2012", "stinn_xin_2014"]

# Столбцы CIGR 2002, табл. 2.1: страна и суточный привес, г
FeedLevel = Literal["S", "NL700", "NL750", "NL800", "DK700", "DK800", "DK900"]

# Корм и пол — CIGR 2002, табл. 3.1 (с. 23, из CIGR 1984), поправка ks к явному
# теплу свиней: часть явного тепла уходит на испарение с мокрого пола и жидкого
# корма — воздух она не греет, а пар добавляет
FloorFeed = Literal["dry_feed_dry_floor", "dry_feed_average_floor", "wet_feed_wet_floor"]
KS_BY_FLOOR_FEED: dict[str, float] = {
    "dry_feed_dry_floor": 1.0,
    "dry_feed_average_floor": 0.95,
    "wet_feed_wet_floor": 0.9,
}

METHOD_SOURCE = {
    "cigr2002": "cigr_2002",
    "brown_brandl_2004": "brown_brandl_2004",
    "rd_apk_2012": "rd_apk_2012",
    "stinn_xin_2014": "stinn_xin_2014",
}

# Теплота парообразования воды при 20 °C: CIGR 2002, с. 20 — 0.680 Вт·ч/г
LATENT_HEAT_J_PER_G = 0.680 * 3600.0


class MethodNotApplicable(ValueError):
    """Методика не покрывает группу — вызывающий отвечает 422, а не считает чужой формулой."""


@dataclass(frozen=True)
class PigHeat:
    """Тепло одной головы (подсосная свиноматка — вместе с гнездом), Вт; пар г/ч; CO₂ л/ч."""
    total_w: float          # полное, при температуре помещения
    sensible_w: float       # явное на уровне здания — греет воздух
    latent_w: float         # скрытое — уходит с паром
    vapour_g_h: float
    co2_l_h: float
    method: str
    source_id: str
    assumptions: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


def _note(code: str, **params) -> dict:
    return {"code": code, "params": params} if params else {"code": code}


def _lerp_table(x: float, xs: list[float], ys: list[float]) -> float:
    """Линейная интерполяция по возрастающим xs; за краями — крайнее значение."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_right(xs, x)
    t = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
    return ys[i - 1] + (ys[i] - ys[i - 1]) * t


# ─── CIGR 2002 ────────────────────────────────────────────────────────────────

# Табл. 2.1 (с. 11): кратность кормления n по массе 20…120 кг
CIGR_N_MASSES = [20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120]
CIGR_N_TABLE: dict[str, list[float]] = {
    "S":     [3.44, 3.42, 3.46, 3.52, 3.59, 3.20, 2.90, 2.65, 2.45, 2.28, 2.14],
    "NL700": [3.03, 2.79, 2.60, 2.73, 2.78, 2.84, 2.83, 2.74, 2.64, 2.52, 2.36],
    "NL750": [3.03, 2.91, 3.23, 3.19, 3.05, 3.19, 3.10, 2.91, 2.69, 2.50, 2.35],
    "NL800": [3.03, 3.02, 3.50, 3.35, 3.32, 3.43, 3.26, 2.99, 2.76, 2.57, 2.41],
    "DK700": [3.37, 3.33, 3.36, 3.27, 3.25, 3.12, 2.82, 2.58, 2.39, 2.22, 2.08],
    "DK800": [3.39, 3.25, 3.22, 3.16, 3.16, 3.12, 3.04, 2.79, 2.57, 2.40, 2.25],
    "DK900": [3.39, 3.25, 3.43, 3.41, 3.40, 3.40, 3.38, 3.18, 2.98, 2.78, 2.60],
}

# По умолчанию — DK 900 г/сут: привес современной генетики (PIC, Topigs), и у
# тяжёлых свиней, которые задают летнюю вентиляцию, наименьший недобор против
# Brown-Brandl 2004 ур. 13 (100 кг: 234 Вт против 251; шведская норма — 215).
# Сравнение столбцов — docs/sources.md, 1.4
DEFAULT_FEED_LEVEL: FeedLevel = "DK900"

# Ниже этой массы — ур. 6 для поросят: табл. 2.1 начинается с 20 кг
CIGR_PIGLET_MAX_KG = 20.0

# n поросёнка 2 кг — Brown-Brandl 2004, табл. 3 (со ссылкой на Pedersen 2002)
PIGLET_N_AT_2KG = 4.1

# Снижение полного тепла на °C выше 20, ‰ от 1000 Вт: ур. 25 — 12 для свиней;
# для малых поросят CIGR (с. 15, 18) рекомендует 20 (у Ota et al. 4–17 кг — 33)
CIGR_TEMP_COEFF = 12.0
CIGR_TEMP_COEFF_PIGLET = 20.0

# CO₂ на 1000 Вт полного тепла ПРИ 20 °C, среднесуточно — гл. 5, с. 37
CIGR_CO2_M3H_PER_HPU = 0.185

# Y2 и Y1 по умолчанию — ур. 9 и 10
CIGR_SOW_DAILY_GAIN_KG = 0.18      # «pregnant sow = 0.18 kg/day»
CIGR_MILK_KG_DAY = 6.0             # «Y1 = milk production, 6 kg/day» — устарело, см. limits


def _cigr_n(weight_kg: float, feed_level: str) -> float:
    if weight_kg < CIGR_PIGLET_MAX_KG:
        # От n = 4.1 при 2 кг линейно до значения столбца при 20 кг: стык с
        # табл. 2.1 без скачка n (скачок самой формулы на стыке ур. 6 и 8 остаётся)
        n20 = CIGR_N_TABLE[feed_level][0]
        return _lerp_table(weight_kg, [2.0, CIGR_PIGLET_MAX_KG], [PIGLET_N_AT_2KG, n20])
    return _lerp_table(weight_kg, CIGR_N_MASSES, CIGR_N_TABLE[feed_level])


def _cigr_growing_total_20(weight_kg: float, n: float) -> float:
    """Ур. 6 (поросята) или 8 (растущие): поддержание + (1 − KY)·(n − 1)·поддержание."""
    if weight_kg < CIGR_PIGLET_MAX_KG:
        maintenance = 7.4 * weight_kg ** 0.66
    else:
        maintenance = 5.09 * weight_kg ** 0.75
    ky = 0.47 + 0.003 * weight_kg
    return maintenance + (1.0 - ky) * (n * maintenance - maintenance)


def _total_hpu(t_c: float, coeff: float) -> float:
    """Полное тепло 1 hpu при t, Вт (ур. 25): 1000 + coeff·(20 − t), не меньше 0."""
    return max(0.0, 1000.0 + coeff * (20.0 - t_c))


def _cigr_sensible_hpu(group: str, t_c: float, coeff: float) -> float:
    """Явное тепло 1 hpu на уровне здания, Вт, в пределах [0, полное]."""
    total = _total_hpu(t_c, coeff)
    if group == "growing":
        # Ур. 32: откорм на частично щелевом полу; доращивание — «see fattening pigs» (п. 3.2.1)
        s = 0.62 * total - 1.15e-7 * t_c ** 6
    else:
        # Ур. 30: «where no specific information is available» — для свиноматок
        # и хряков своей формулы уровня здания в CIGR нет
        s = 0.8 * total - 0.38 * t_c ** 2
    return min(max(s, 0.0), total)


def _cigr(group: str, weight_kg: float, t_c: float, feed_level: str,
          pregnancy_day: float, daily_gain_kg: Optional[float],
          milk_kg_day: Optional[float]) -> tuple[float, float, float, list[dict], list[dict]]:
    """Полное при 20 °C, полное при t, явное при t; плюс допущения и предупреждения."""
    assumptions: list[dict] = []
    warnings: list[dict] = []
    coeff = CIGR_TEMP_COEFF

    if group == "growing":
        n = _cigr_n(weight_kg, feed_level)
        total_20 = _cigr_growing_total_20(weight_kg, n)
        assumptions.append(_note("feed_level", feed_level=feed_level, n=round(n, 2)))
        if weight_kg < CIGR_PIGLET_MAX_KG:
            coeff = CIGR_TEMP_COEFF_PIGLET
            assumptions.append(_note("piglet_temperature_coefficient", pct_per_c=2.0))
        if weight_kg > CIGR_N_MASSES[-1]:
            warnings.append(_note("feed_level_extrapolated", weight_kg=weight_kg,
                                  table_max_kg=CIGR_N_MASSES[-1]))
    elif group == "lactating_sow":
        y1 = CIGR_MILK_KG_DAY if milk_kg_day is None else milk_kg_day
        total_20 = 4.85 * weight_kg ** 0.75 + 28.0 * y1
        assumptions.append(_note("milk_yield", kg_day=y1, source_default=CIGR_MILK_KG_DAY))
    else:
        # Хряк — привес по умолчанию 0: у CIGR значение дано только для
        # свиноматки и свинки, у взрослого хряка прирост мал
        default_gain = 0.0 if group == "boar" else CIGR_SOW_DAILY_GAIN_KG
        y2 = default_gain if daily_gain_kg is None else daily_gain_kg
        total_20 = 4.85 * weight_kg ** 0.75 + 8e-5 * pregnancy_day ** 3 + 76.0 * y2
        assumptions.append(_note("daily_gain", kg_day=y2))

    total_t = total_20 * _total_hpu(t_c, coeff) / 1000.0
    sensible_t = total_20 * _cigr_sensible_hpu(group, t_c, coeff) / 1000.0
    return total_20, total_t, sensible_t, assumptions, warnings


# ─── Brown-Brandl 2004 ────────────────────────────────────────────────────────

BB_MIN_KG, BB_MAX_KG = 10.0, 100.0     # диапазон данных регрессии


def _brown_brandl_total(weight_kg: float, t_c: float) -> float:
    """Ур. 13 (генетика 1988–2002): log10(HP, Вт/кг) = 1.189 − 0.005·t − 0.345·log10(m)."""
    return weight_kg * 10.0 ** (1.189 - 0.005 * t_c - 0.345 * math.log10(weight_kg))


# ─── РД-АПК 1.10.02.04-12 ─────────────────────────────────────────────────────

# Табл. 15, при 10 °C: масса, кг → (общая теплота, свободная теплота, кДж/ч; пар, г/ч).
# Растущие — строки «поросята до 2-мес.», «отъёмыши» и «ремонтный и откормочный
# молодняк» одной кривой: на стыках (15 и 40 кг) таблица даёт одинаковые значения
RD_GROWING = [
    (10, 357.9, 257.5, 41.1), (15, 458.3, 329.6, 52.6), (20, 512.5, 369.2, 58.8),
    (25, 550.0, 394.2, 62.8), (30, 595.8, 429.2, 68.4), (35, 654.2, 470.8, 75.2),
    (40, 716.7, 516.7, 82.1), (50, 820.8, 591.7, 94.1), (60, 904.2, 650.0, 104.0),
    (70, 987.5, 712.5, 114.0), (80, 1066.7, 766.7, 123.0), (90, 1133.3, 816.7, 130.0),
    (100, 1204.2, 866.7, 138.0), (110, 1258.3, 904.2, 144.0), (120, 1308.3, 941.7, 150.0),
    (130, 1358.3, 979.2, 156.0),
]
# Хряк 200 кг: в таблице свободная теплота «2116,7 (292)» — больше общей. 292 ккал/ч
# × 4.1868 = 1222.5 кДж/ч; с этим значением пар сходится: (1687.5 − 1222.5)/2.45 = 190 г/ч
RD_BOAR = [(200, 1687.5, 1222.5, 194.0), (300, 2154.2, 1550.0, 247.0)]
RD_SOW_DRY_GESTATING = [(150, 1170.8, 841.7, 134.0), (200, 1345.8, 970.8, 155.0)]   # до 105 дней
RD_SOW_LATE_GESTATING = [(150, 1412.5, 1016.7, 162.0), (200, 1595.8, 1150.0, 183.0)]  # 105–114 дней
RD_SOW_LACTATING = [(150, 2775.0, 2000.0, 319.0), (200, 3212.5, 2312.5, 369.0)]     # с поросятами

RD_LATE_GESTATION_DAY = 105

# Табл. 16: температура → множители (общая теплота, свободная теплота, пар)
RD_TEMP = [(-5, 1.34, 1.59, 0.72), (0, 1.14, 1.25, 0.85), (5, 1.06, 1.08, 0.98), (10, 1.00, 1.00, 1.00),
           (15, 0.94, 0.86, 1.13), (20, 0.90, 0.67, 1.50), (25, 0.86, 0.42, 2.00), (30, 0.87, 0.24, 2.50)]

# Прим. 4: CO₂ 0.036 л на 1 кДж/ч общей теплоты
RD_CO2_L_PER_KJ_H = 0.036


def _rd_rows(group: str, pregnancy_day: float) -> list[tuple]:
    if group == "growing":
        return RD_GROWING
    if group == "boar":
        return RD_BOAR
    if group == "lactating_sow":
        return RD_SOW_LACTATING
    if group == "gestating_sow" and pregnancy_day >= RD_LATE_GESTATION_DAY:
        return RD_SOW_LATE_GESTATING
    return RD_SOW_DRY_GESTATING


# ─── Stinn & Xin 2014 ─────────────────────────────────────────────────────────
# Trans. ASABE 57(5):1517–1528: непрямая калориметрия (O₂/CO₂ за вычетом горелок и
# навоза) на комплексе 4300 свиноматок, 16 месяцев. Тепло — на уровне ПОМЕЩЕНИЯ:
# скрытое включает испарение с пола и навоза (как CIGR ур. 32). Средние за сутки (TWA).
#
# Табл. 3 — супоросные, Вт/кг массы свиноматки, при 20 и 25 °C (±2.5):
#   (THP, LHP, SHP, RQ); B/EG — осеменение и ранняя супоросность (до ~40-го дня),
#   LG — поздняя. Массы: B/EG 204 кг, LG 219 кг (аннотация)
SX_GESTATION = {
    "B/EG": {20.0: (1.89, 0.74, 1.15, 1.07), 25.0: (1.82, 1.08, 0.74, 1.19)},
    "LG":   {20.0: (1.57, 0.57, 1.00, 1.09), 25.0: (1.26, 0.87, 0.39, 1.22)},
}
SX_EARLY_GESTATION_END_DAY = 40      # «housed in the B/EG barn … until approximately day 40»

# Табл. 4 — подсосные С ГНЕЗДОМ, 20–26 °C, Вт/кг массы СВИНОМАТКИ + ГНЕЗДА (сноска [a]):
#   стадия → (масса свиноматки с гнездом, THP, LHP, SHP, RQ); стадии — по дню лактации
SX_LACTATION = (
    (-1, 222.0, 2.74, 1.83, 0.91, 1.05),   # до опороса
    (0,  235.0, 3.35, 1.98, 1.37, 1.06),   # день 0–6
    (7,  247.0, 4.15, 1.92, 2.23, 1.05),   # день 7–12
    (13, 256.0, 4.38, 1.93, 2.45, 1.06),   # день 13–18 (отъём 18–22)
)
SX_LACTATION_T_RANGE = (20.0, 26.0)


def _sx_co2_l_h(thp_w: float, rq: float) -> float:
    """CO₂ из THP и RQ по ур. 1–2 статьи: THP = 16.18·O₂ + 5.02·CO₂ (мл/с), RQ = CO₂/O₂."""
    co2_ml_s = thp_w / (16.18 / rq + 5.02)
    return co2_ml_s * 3.6


def _stinn_xin(group: str, weight_kg: float, t_c: float, pregnancy_day: float,
               lactation_day: Optional[float], litter_mass_kg: Optional[float]):
    """(полное, явное, скрытое Вт; CO₂ л/ч; допущения; предупреждения)."""
    assumptions: list[dict] = []
    warnings: list[dict] = []
    if group in ("dry_sow", "gestating_sow"):
        # Холостые после отъёма стоят в том же секторе B/EG
        stage = "LG" if group == "gestating_sow" and pregnancy_day >= SX_EARLY_GESTATION_END_DAY else "B/EG"
        if not 17.5 <= t_c <= 27.5:
            warnings.append(_note("temperature_outside_source_range", source_id="stinn_xin_2014",
                                  min_c=17.5, max_c=27.5))
        tt = min(max(t_c, 20.0), 25.0)
        lo, hi = SX_GESTATION[stage][20.0], SX_GESTATION[stage][25.0]
        k = (tt - 20.0) / 5.0
        thp, lhp, shp, rq = (a + (b - a) * k for a, b in zip(lo, hi))
        assumptions.append(_note("gestation_stage", stage=stage))
        mass = weight_kg
    else:  # lactating_sow
        if lactation_day is None:
            raise ValueError("lactation_day is required for lactating_sow with stinn_xin_2014 (−1 — до опороса)")
        row = SX_LACTATION[0]
        for r in SX_LACTATION:
            if lactation_day >= r[0]:
                row = r
        _, table_mass, thp, lhp, shp, rq = row
        if not SX_LACTATION_T_RANGE[0] <= t_c <= SX_LACTATION_T_RANGE[1]:
            warnings.append(_note("temperature_outside_source_range", source_id="stinn_xin_2014",
                                  min_c=SX_LACTATION_T_RANGE[0], max_c=SX_LACTATION_T_RANGE[1]))
        if litter_mass_kg is None:
            # Удельное тепло — на массу свиноматки с гнездом; без массы гнезда берём
            # массу самой таблицы (свиноматка 223 кг с гнездом этой стадии)
            mass = table_mass
            assumptions.append(_note("sow_and_litter_mass_from_source", mass_kg=table_mass))
        else:
            mass = weight_kg + litter_mass_kg
    total = thp * mass
    return total, shp * mass, lhp * mass, _sx_co2_l_h(total, rq), assumptions, warnings


# ─── Общий вход ───────────────────────────────────────────────────────────────

def pig_heat(
    group: Group,
    weight_kg: float,
    t_inside_c: float,
    method: Method = "cigr2002",
    feed_level: FeedLevel = DEFAULT_FEED_LEVEL,
    pregnancy_day: Optional[float] = None,
    daily_gain_kg: Optional[float] = None,
    milk_kg_day: Optional[float] = None,
    floor_feed: FloorFeed = "dry_feed_dry_floor",
    lactation_day: Optional[float] = None,
    litter_mass_kg: Optional[float] = None,
) -> PigHeat:
    """Тепло, пар и CO₂ одной головы при температуре помещения."""
    if group == "gestating_sow" and pregnancy_day is None:
        # День супоросности меняет тепло на десятки процентов к концу срока
        # (8·10⁻⁵·p³: +106 Вт на 110-й день) — молча подставлять нельзя
        raise ValueError("pregnancy_day is required for gestating_sow")
    p = 0.0 if pregnancy_day is None else float(pregnancy_day)
    source_id = METHOD_SOURCE[method]
    assumptions: list[dict] = []
    warnings: list[dict] = []

    if method == "cigr2002":
        total_20, total_w, sensible_w, assumptions, warnings = _cigr(
            group, weight_kg, t_inside_c, feed_level, p, daily_gain_kg, milk_kg_day)
        latent_w = total_w - sensible_w
        vapour = latent_w * 3600.0 / LATENT_HEAT_J_PER_G
        co2 = CIGR_CO2_M3H_PER_HPU * total_20       # м³/ч на 1000 Вт = л/ч на 1 Вт

    elif method == "brown_brandl_2004":
        if group != "growing":
            raise MethodNotApplicable(f"{method} covers growing pigs only, not {group}")
        if not BB_MIN_KG <= weight_kg <= BB_MAX_KG:
            warnings.append(_note("weight_outside_source_range", source_id=source_id,
                                  min_kg=BB_MIN_KG, max_kg=BB_MAX_KG))
        total_w = _brown_brandl_total(weight_kg, t_inside_c)
        total_20 = _brown_brandl_total(weight_kg, 20.0)
        # Обзор разделения на явное и скрытое не даёт — доля явного по CIGR ур. 32
        hpu_total = _total_hpu(t_inside_c, CIGR_TEMP_COEFF)
        share = _cigr_sensible_hpu("growing", t_inside_c, CIGR_TEMP_COEFF) / hpu_total if hpu_total > 0 else 0.0
        sensible_w = total_w * share
        latent_w = total_w - sensible_w
        vapour = latent_w * 3600.0 / LATENT_HEAT_J_PER_G
        co2 = CIGR_CO2_M3H_PER_HPU * total_20
        assumptions.append(_note("sensible_share_from_cigr", share=round(share, 3)))

    elif method == "stinn_xin_2014":
        if group not in ("dry_sow", "gestating_sow", "lactating_sow"):
            raise MethodNotApplicable(f"{method} covers sows only, not {group}")
        total_w, sensible_w, latent_w, co2, assumptions, warnings = _stinn_xin(
            group, weight_kg, t_inside_c, p, lactation_day, litter_mass_kg)
        vapour = latent_w * 3600.0 / LATENT_HEAT_J_PER_G

    else:  # rd_apk_2012
        rows = _rd_rows(group, p)
        masses = [r[0] for r in rows]
        if not masses[0] <= weight_kg <= masses[-1]:
            warnings.append(_note("weight_outside_source_range", source_id=source_id,
                                  min_kg=float(masses[0]), max_kg=float(masses[-1])))
        temps = [r[0] for r in RD_TEMP]
        if not temps[0] <= t_inside_c <= temps[-1]:
            warnings.append(_note("temperature_outside_source_range", source_id=source_id,
                                  min_c=float(temps[0]), max_c=float(temps[-1])))
        total_10 = _lerp_table(weight_kg, masses, [r[1] for r in rows])
        free_10 = _lerp_table(weight_kg, masses, [r[2] for r in rows])
        vapour_10 = _lerp_table(weight_kg, masses, [r[3] for r in rows])
        k_total = _lerp_table(t_inside_c, temps, [r[1] for r in RD_TEMP])
        k_free = _lerp_table(t_inside_c, temps, [r[2] for r in RD_TEMP])
        k_vapour = _lerp_table(t_inside_c, temps, [r[3] for r in RD_TEMP])
        total_w = total_10 * k_total / 3.6           # кДж/ч → Вт
        sensible_w = min(free_10 * k_free / 3.6, total_w)
        latent_w = total_w - sensible_w
        # Пар — из таблицы, а не из скрытого тепла: так в норме, и это пар от
        # животного, без испарения с пола (раздел об испарении в РД-АПК нет)
        vapour = vapour_10 * k_vapour
        co2 = RD_CO2_L_PER_KJ_H * total_10
        assumptions.append(_note("vapour_animal_level"))

    # Поправка ks (одинаково для всех методик): доля (1 − ks) явного тепла идёт
    # на испарение — переходит в скрытое и добавляет пар
    ks = KS_BY_FLOOR_FEED[floor_feed]
    if ks < 1.0:
        evaporation_w = sensible_w * (1.0 - ks)
        sensible_w -= evaporation_w
        latent_w += evaporation_w
        vapour += evaporation_w * 3600.0 / LATENT_HEAT_J_PER_G
        assumptions.append(_note("floor_feed", floor_feed=floor_feed, ks=ks))

    return PigHeat(
        total_w=round(total_w, 1),
        sensible_w=round(sensible_w, 1),
        latent_w=round(latent_w, 1),
        vapour_g_h=round(vapour, 1),
        co2_l_h=round(co2, 1),
        method=method,
        source_id=source_id,
        assumptions=assumptions,
        warnings=warnings,
    )
