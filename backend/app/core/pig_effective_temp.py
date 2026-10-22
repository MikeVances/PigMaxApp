"""
Эффективная температура свиньи — аналог «кажущейся температуры» бройлеров MINMAXapp.

У бройлеров её считают по таблице скорость × температура × RH. У свиней такой
модели нет — только эмпирика (docs/sources.md, 4.1), из неё и собрано:

  T_eff = T_воздуха − охлаждение_скоростью(v) + поправка_пола

Охлаждение скоростью — McFarlane 2000 (через OMAFRA, Chambers 2012, табл. 1),
совпадает с таблицей Hubbard Feeds: 30 / 90 / 300 fpm → −4 / −7 / −10 °C.
Между точками — линейно; быстрее 300 fpm данных нет, эффект держим на −10 °C.

Пол — Close 1987, табл. 3: сдвиг НИЖНЕЙ КРИТИЧЕСКОЙ ТЕМПЕРАТУРЫ относительно
деревянного пола: солома −4, асфальт −2, бетонные щели +3 °C. Заголовок столбца
«to be added to air temperature» понимаем как сдвиг критической температуры:
буквальное прибавление к воздуху сделало бы солому «холоднее» щелей, а она теплее.
Уставки генетики (PIC — «сухие щелевые полы») уже заданы для щелей, поэтому
поправка — ОТНОСИТЕЛЬНО ЩЕЛЕЙ: на щелях 0, на соломе +7 °C теплее, на асфальте +5,
на дереве +3. Холодный мокрый пол — Mount 1975: «эквивалентно снижению температуры
воздуха на 7–10 °C» — берём середину 8.5 относительно сухого пола (HYPOTHESIS).

Обе поправки — эмпирика по ощущению свиньи, а не физическая формула, как у
бройлеров; в ответе это помечено.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

FPM_TO_MS = 0.00508

# McFarlane 2000: (скорость, м/с; охлаждение, °C)
WIND_CHILL = ((0.0, 0.0), (30 * FPM_TO_MS, 4.0), (90 * FPM_TO_MS, 7.0), (300 * FPM_TO_MS, 10.0))

Floor = Literal["concrete_slats", "wood", "asphalt", "straw", "wet_concrete"]

# Close 1987 табл. 3: сдвиг критической температуры относительно дерева, °C
_LCT_SHIFT_VS_WOOD = {"wood": 0.0, "straw": -4.0, "asphalt": -2.0, "concrete_slats": 3.0}
# Mount 1975: холодный мокрый пол ≈ воздух на 7–10 °C холоднее (середина) — от щелей
WET_CONCRETE_PENALTY_C = 8.5


def _note(code: str, **params) -> dict:
    return {"code": code, "params": params} if params else {"code": code}


def wind_chill_c(v_ms: float) -> float:
    """Снижение эффективной температуры скоростью воздуха у свиней, °C (≥ 0)."""
    v = max(v_ms, 0.0)
    for (v0, c0), (v1, c1) in zip(WIND_CHILL, WIND_CHILL[1:]):
        if v <= v1:
            return c0 + (c1 - c0) * (v - v0) / (v1 - v0)
    return WIND_CHILL[-1][1]


def floor_correction_c(floor: Floor) -> float:
    """Поправка пола к эффективной температуре относительно щелевого пола, °C (+ — теплее)."""
    if floor == "wet_concrete":
        return -WET_CONCRETE_PENALTY_C
    return _LCT_SHIFT_VS_WOOD["concrete_slats"] - _LCT_SHIFT_VS_WOOD[floor]


def speed_for_cooling_ms(cooling_c: float) -> float | None:
    """Скорость, дающая нужное охлаждение, м/с; None — больше, чем даёт таблица (10 °C)."""
    if cooling_c <= 0:
        return 0.0
    for (v0, c0), (v1, c1) in zip(WIND_CHILL, WIND_CHILL[1:]):
        if cooling_c <= c1:
            return v0 + (v1 - v0) * (cooling_c - c0) / (c1 - c0)
    return None


@dataclass(frozen=True)
class EffectiveTemperature:
    t_effective_c: float
    wind_chill_c: float
    floor_correction_c: float
    above_setpoint_c: float | None          # T_eff − уставка; > 0 — свиньям жарче нужного
    speed_to_setpoint_ms: float | None      # скорость, сводящая T_eff к уставке; None — не хватит
    sources: list[str]
    assumptions: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


def effective_temperature(t_air_c: float, v_ms: float = 0.0, floor: Floor = "concrete_slats",
                          setpoint_c: float | None = None) -> EffectiveTemperature:
    """Эффективная температура у свиней и скорость воздуха, нужная, чтобы дойти до уставки."""
    chill = wind_chill_c(v_ms)
    fc = floor_correction_c(floor)
    t_eff = t_air_c - chill + fc
    assumptions = [_note("effective_temperature_empirical")]
    warnings: list[dict] = []
    if v_ms > WIND_CHILL[-1][0]:
        warnings.append(_note("air_speed_beyond_data", max_ms=round(WIND_CHILL[-1][0], 2)))
    if floor == "wet_concrete":
        assumptions.append(_note("wet_floor_midpoint", penalty_c=WET_CONCRETE_PENALTY_C))

    above = speed = None
    if setpoint_c is not None:
        above = round(t_eff - setpoint_c, 1)
        # Скорость считаем от неподвижного воздуха: какое охлаждение нужно при этом полу
        need = t_air_c + fc - setpoint_c
        speed = speed_for_cooling_ms(need)
        if speed is None:
            warnings.append(_note("air_speed_cannot_reach_setpoint", cooling_needed_c=round(need, 1),
                                  max_cooling_c=WIND_CHILL[-1][1]))
        else:
            speed = round(speed, 2)

    return EffectiveTemperature(
        t_effective_c=round(t_eff, 1), wind_chill_c=round(chill, 1), floor_correction_c=fc,
        above_setpoint_c=above, speed_to_setpoint_ms=speed,
        sources=["mcfarlane_2000", "close_1987"], assumptions=assumptions, warnings=warnings,
    )
