"""
Минимальная вентиляция = max(норма на голову, по влаге, по CO₂) — как в MINMAXapp.

До этапа 2 минимальная вентиляция бралась только из таблицы PIC 4.1, а предел
влажности 65 % выводился в ответе, но в расчёт не входил. PIC сам пишет (с. 17):
«когда влажность выше 65 %, а снаружи холоднее уставки, воздухообмен нужно
увеличить» — таблица этого не делает, баланс влаги делает.

Три критерия, каждый — расход НА ГОЛОВУ, м³/ч:

  co2      — выдыхаемый CO₂ разбавить до предела (CIGR 2002, ур. 36: среднесуточно);
  moisture — пар здания вынести наружным воздухом до предела RH (баланс влагосодержания);
  norm     — норма источника: PIC (табл. 4.1 и руководство по свиноматкам 2021)
             или РД-АПК 1.10.02.04-12 (30/45/60 м³/ч на 100 кг живой массы).

Определяющий критерий называется в ответе (`basis`), как `qmin_basis` в MINMAXapp.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from . import psychro
from .pig_heat import DEFAULT_FEED_LEVEL, FeedLevel, FloorFeed, Group, Method, PigHeat, pig_heat
from .pig_profile import interpolate_by_weight

NormSource = Literal["pic", "rd_apk_2012", "none"]
Period = Literal["cold", "transition", "warm"]

# Предел CO₂ по умолчанию — PIC 2019 (с. 4-10) и Klimaatplatform 2021: 3000 ppm.
# РД-АПК (п. 14.4) строже — 0.2 % об. = 2000 ppm; задаётся параметром
DEFAULT_CO2_LIMIT_PPM = 3000.0

# CO₂ наружного воздуха: среднемировой уровень 2024 г. ≈ 423 ppm (NOAA GML).
# 400 ppm, как в старых расчётах, завышает разность концентраций на ~1 %
DEFAULT_CO2_OUTDOOR_PPM = 420.0

# Предел RH по умолчанию — PIC 2019 (с. 14, 17): «не должен превышать 65 %».
# РД-АПК табл. 17 допускает 70–75 % по группам — задаётся параметром
DEFAULT_RH_MAX_PCT = 65.0

# PIC Gilt & Sow Management Guidelines 2021 — минимальная вентиляция зимой, cfm/гол:
# табл. 3.1 (осеменение и супоросность) — 12; табл. 5.2 (опорос) — 20 на станок
CFM_TO_M3H = 1.699011
PIC_SOW_MIN_CFM = {"gestating_sow": 12.0, "dry_sow": 12.0, "boar": 12.0, "lactating_sow": 20.0}

# РД-АПК п. 14.6: «не менее: в холодный период — 30 м³/ч, переходный — 45 м³/ч,
# тёплый период — 60 м³/ч на 1 ц живой массы свиней»
RD_NORM_M3H_PER_100KG = {"cold": 30.0, "transition": 45.0, "warm": 60.0}

# Тёплый период — «при температуре наружного воздуха выше 10 °С» (РД-АПК, прим. 3
# к табл. 17). Граница холодного и переходного в документе НЕ задана — 0 °C наша
# (HYPOTHESIS), поэтому период можно передать явно
WARM_ABOVE_C = 10.0
COLD_BELOW_C = 0.0


@dataclass(frozen=True)
class MinVentilation:
    per_head_m3h: float
    basis: str                              # "co2" | "moisture" | "norm"
    co2_m3h: float
    moisture_m3h: Optional[float]           # None — снаружи не суше цели, влагу не вынести
    norm_m3h: Optional[float]
    humidity_uncontrollable: bool
    delta_w_g_per_kg: float
    heat: PigHeat
    sources: list[str]
    assumptions: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


def _note(code: str, **params) -> dict:
    return {"code": code, "params": params} if params else {"code": code}


def season_period(t_outside_c: float) -> Period:
    if t_outside_c > WARM_ABOVE_C:
        return "warm"
    return "transition" if t_outside_c >= COLD_BELOW_C else "cold"


def co2_m3h(co2_l_h: float, limit_ppm: float, outdoor_ppm: float) -> float:
    """Расход, разбавляющий CO₂ до предела: (л/ч / 1000) / (Δppm · 10⁻⁶) — ур. 36 CIGR."""
    return (co2_l_h / 1000.0) / ((limit_ppm - outdoor_ppm) * 1e-6)


def moisture_m3h(vapour_g_h: float, t_inside_c: float, rh_max_pct: float,
                 t_outside_c: float, rh_outside_pct: float, p_pa: float) -> tuple[Optional[float], float]:
    """
    Расход, выносящий пар при влажности на пределе: Q = G / (ρ_св · ΔW).

    ρ_св — сухой воздух в кубометре при условиях ВНУТРИ (вентиляторы вытяжные и
    меряют воздух помещения). Второе значение — ΔW, г/кг. ΔW ≤ 0 — снаружи воздух
    не суше целевого: никакой расход не удержит RH, нужен подогрев или осушение.
    """
    w_in = psychro.humidity_ratio(t_inside_c, rh_max_pct, p_pa)
    w_out = psychro.humidity_ratio(t_outside_c, rh_outside_pct, p_pa)
    delta_w = w_in - w_out
    if delta_w <= 0.0:
        return None, delta_w * 1000.0
    rho_da = psychro.dry_air_kg_per_m3(t_inside_c, w_in, p_pa)
    return (vapour_g_h / 1000.0) / (rho_da * delta_w), delta_w * 1000.0


def norm_m3h(norm: NormSource, group: str, weight_kg: float, period: Period,
             litter_mass_kg: Optional[float]) -> tuple[Optional[float], Optional[str], list[dict]]:
    """Норма источника на голову, м³/ч; id источника; допущения."""
    notes: list[dict] = []
    if norm == "none":
        return None, None, notes
    if norm == "pic":
        if group == "growing":
            return interpolate_by_weight(weight_kg).min_vent_m3_head_h, "pic_2019_w2f", notes
        if group == "boar":
            # Своей нормы для хряков у PIC нет; хряки стоят в секторе осеменения
            notes.append(_note("boar_norm_from_breeding_area"))
        return round(PIC_SOW_MIN_CFM[group] * CFM_TO_M3H, 1), "pic_2021_sow", notes
    # rd_apk_2012 — на 100 кг живой массы
    mass = weight_kg
    if group == "lactating_sow":
        if litter_mass_kg is None:
            # Норма — на живую массу «свиней», то есть и поросят; без их массы
            # норма для станка занижена — к отъёму гнездо весит 60–90 кг
            notes.append(_note("litter_mass_not_counted"))
        else:
            mass += litter_mass_kg
    return RD_NORM_M3H_PER_100KG[period] * mass / 100.0, "rd_apk_2012", notes


def min_ventilation(
    group: Group,
    weight_kg: float,
    t_inside_c: float,
    t_outside_c: float,
    rh_outside_pct: float,
    *,
    rh_max_pct: float = DEFAULT_RH_MAX_PCT,
    co2_limit_ppm: float = DEFAULT_CO2_LIMIT_PPM,
    co2_outdoor_ppm: float = DEFAULT_CO2_OUTDOOR_PPM,
    norm: NormSource = "pic",
    period: Optional[Period] = None,
    altitude_m: float = 0.0,
    method: Method = "cigr2002",
    feed_level: FeedLevel = DEFAULT_FEED_LEVEL,
    floor_feed: FloorFeed = "dry_feed_dry_floor",
    pregnancy_day: Optional[float] = None,
    daily_gain_kg: Optional[float] = None,
    milk_kg_day: Optional[float] = None,
    litter_mass_kg: Optional[float] = None,
) -> MinVentilation:
    """Минимальная вентиляция на голову при заданных условиях внутри и снаружи."""
    if co2_limit_ppm <= co2_outdoor_ppm:
        raise ValueError("co2_limit_ppm must exceed co2_outdoor_ppm")

    heat = pig_heat(group, weight_kg, t_inside_c, method=method, feed_level=feed_level,
                    pregnancy_day=pregnancy_day, daily_gain_kg=daily_gain_kg,
                    milk_kg_day=milk_kg_day, floor_feed=floor_feed)
    assumptions = list(heat.assumptions)
    warnings = list(heat.warnings)
    sources = [heat.source_id]

    # ── CO₂ ───────────────────────────────────────────────────────────────────
    q_co2 = co2_m3h(heat.co2_l_h, co2_limit_ppm, co2_outdoor_ppm)
    # 0.185 м³/ч на hpu — среднесуточно; днём, в часы активности, выделение выше
    # (CIGR гл. 4: амплитуда 0.43 на откорме) — минимум по среднему это допускает
    assumptions.append(_note("co2_daily_average", limit_ppm=co2_limit_ppm, outdoor_ppm=co2_outdoor_ppm))

    # ── Влага ─────────────────────────────────────────────────────────────────
    p_pa = psychro.pressure_at_altitude_pa(altitude_m)
    q_moist, delta_w = moisture_m3h(heat.vapour_g_h, t_inside_c, rh_max_pct,
                                    t_outside_c, rh_outside_pct, p_pa)
    uncontrollable = q_moist is None
    if uncontrollable:
        warnings.append(_note("humidity_uncontrollable", rh_max_pct=rh_max_pct,
                              delta_w_g_per_kg=round(delta_w, 2)))

    # ── Норма ─────────────────────────────────────────────────────────────────
    if period is None:
        period = season_period(t_outside_c)
        if norm == "rd_apk_2012":
            assumptions.append(_note("period_from_outside_temperature", period=period,
                                     warm_above_c=WARM_ABOVE_C, cold_below_c=COLD_BELOW_C))
    q_norm, norm_source, norm_notes = norm_m3h(norm, group, weight_kg, period, litter_mass_kg)
    assumptions.extend(norm_notes)
    if norm_source:
        sources.append(norm_source)

    # ── Максимум ──────────────────────────────────────────────────────────────
    # Порядок кандидатов задаёт, кого назвать при равенстве: физика раньше нормы
    candidates = [("co2", q_co2)]
    if q_moist is not None:
        candidates.append(("moisture", q_moist))
    if q_norm is not None:
        candidates.append(("norm", q_norm))
    basis, q = max(candidates, key=lambda c: c[1])

    return MinVentilation(
        per_head_m3h=round(q, 1),
        basis=basis,
        co2_m3h=round(q_co2, 1),
        moisture_m3h=round(q_moist, 1) if q_moist is not None else None,
        norm_m3h=round(q_norm, 1) if q_norm is not None else None,
        humidity_uncontrollable=uncontrollable,
        delta_w_g_per_kg=round(delta_w, 2),
        heat=heat,
        sources=sources,
        assumptions=assumptions,
        warnings=warnings,
    )
