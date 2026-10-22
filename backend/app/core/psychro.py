"""
Психрометрия влажного воздуха.

Перенесено из MINMAXapp (backend/app/core/psychro.py, 59390c7) — docs/porting/house_census.md.
Отличие (ADAPT): давление площадки передаётся явно (из высоты в запросе), а не
читается из настроек хозяйства (site_pressure_pa) — у PigMaxApp их нет.

Уравнения — ASHRAE Handbook Fundamentals 2017, гл. 1 (номера в скобках).
Давление насыщения — формула Магнуса: против Hyland–Wexler (ASHRAE) расхождение
< 0.3 % в рабочем диапазоне.
"""
from __future__ import annotations

import math

P_ATM_PA = 101_325.0
# Отношение молярных масс воды и сухого воздуха (ASHRAE: 0.621945)
_EPS = 0.621945
# Газовая постоянная сухого воздуха, кДж/(кг·К) (ASHRAE: 0.287042)
_R_DA = 0.287042


def pressure_at_altitude_pa(altitude_m: float) -> float:
    # Source: core/psychro.py -> pressure_at_altitude_pa
    """
    Атмосферное давление на высоте, Па — стандартная атмосфера (3):
    p = 101.325·(1 − 2.25577·10⁻⁵·Z)^5.2559 кПа. На 500 м — 95.5 кПа: воздух
    на 6 % реже, и при 101.3 кПа баланс влаги завышал бы массу воздуха на столько же.
    """
    return 101_325.0 * (1.0 - 2.25577e-5 * altitude_m) ** 5.2559


def sat_pressure_pa(t_c: float) -> float:
    # Source: core/psychro.py -> sat_pressure_pa
    """Давление насыщенного пара над водой, Па (Магнус: 611.2·exp(17.67·t/(t+243.5)))."""
    return 611.2 * math.exp(17.67 * t_c / (t_c + 243.5))


def humidity_ratio(t_c: float, rh_pct: float, p_pa: float = P_ATM_PA) -> float:
    # Source: core/psychro.py -> humidity_ratio (ADAPT: p явно)
    """
    Влагосодержание W, кг воды / кг сухого воздуха (20): W = 0.621945·pw/(p − pw).

    RH — относительно насыщения НАД ВОДОЙ и ниже нуля: так её дают метеостанции
    (WMO) и так её вводит пользователь.
    """
    p_w = max(rh_pct, 0.0) / 100.0 * sat_pressure_pa(t_c)
    return _EPS * p_w / max(p_pa - p_w, 1.0)


def sat_pressure_ice_pa(t_c: float) -> float:
    # Source: core/psychro.py -> sat_pressure_ice_pa
    """Давление насыщения надо льдом, Па (Магнус для льда, WMO: 22.46 / 272.62)."""
    return 611.2 * math.exp(22.46 * t_c / (t_c + 272.62))


def _w_from_wet_bulb(t_c: float, t_wb: float, p_pa: float) -> float:
    # Source: core/psychro.py -> _w_from_wet_bulb
    """W по температурам сухого и мокрого термометров (33) — выше нуля, (35) — ниже.

    Ниже нуля термометр обледенел: насыщение у его поверхности — надо льдом."""
    if t_wb >= 0.0:
        ws = humidity_ratio(t_wb, 100.0, p_pa)
        return ((2501.0 - 2.326 * t_wb) * ws - 1.006 * (t_c - t_wb)) / (2501.0 + 1.86 * t_c - 4.186 * t_wb)
    p_wi = sat_pressure_ice_pa(t_wb)
    ws = _EPS * p_wi / (p_pa - p_wi)
    return ((2830.0 - 0.24 * t_wb) * ws - 1.006 * (t_c - t_wb)) / (2830.0 + 1.86 * t_c - 2.1 * t_wb)


def wet_bulb_c(t_c: float, rh_pct: float, p_pa: float = P_ATM_PA) -> float:
    # Source: core/psychro.py -> wet_bulb_c (ADAPT: p явно)
    """
    Температура мокрого термометра, °C — решение (33)/(35) относительно t_wb.

    W(t_wb) растёт монотонно, поэтому деление пополам между −60 °C и t_c сходится
    всегда; 60 шагов — точность лучше 1e-12 °C, дешевле любой подгонки.
    """
    w = humidity_ratio(t_c, rh_pct, p_pa)
    lo, hi = -60.0, t_c
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if _w_from_wet_bulb(t_c, mid, p_pa) > w:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2.0


def dew_point_c(t_c: float, rh_pct: float) -> float:
    # Source: core/psychro.py -> dew_point_c
    """Точка росы, °C — обращение формулы Магнуса."""
    g = math.log(max(rh_pct, 0.01) / 100.0 * sat_pressure_pa(t_c) / 611.2)
    return 243.5 * g / (17.67 - g)


def specific_volume_m3_per_kg_da(t_c: float, w: float, p_pa: float = P_ATM_PA) -> float:
    # Source: core/psychro.py -> specific_volume_m3_per_kg_da (ADAPT: p явно)
    """Удельный объём влажного воздуха, м³ на кг СУХОГО воздуха (26)."""
    return _R_DA * (t_c + 273.15) * (1.0 + 1.607858 * w) / (p_pa / 1000.0)


def dry_air_kg_per_m3(t_c: float, w: float, p_pa: float = P_ATM_PA) -> float:
    # Source: core/psychro.py -> dry_air_kg_per_m3 (ADAPT: p явно)
    """Масса сухого воздуха в кубометре влажного — для балансов «на кг сухого воздуха»."""
    return 1.0 / specific_volume_m3_per_kg_da(t_c, w, p_pa)


def moist_air_density(t_c: float, rh_pct: float, p_pa: float = P_ATM_PA) -> float:
    # Source: core/psychro.py -> moist_air_density (ADAPT: p явно)
    """Плотность влажного воздуха, кг/м³ (28): (1 + W) / v — для давлений и расходов."""
    w = humidity_ratio(t_c, rh_pct, p_pa)
    return (1.0 + w) / specific_volume_m3_per_kg_da(t_c, w, p_pa)
