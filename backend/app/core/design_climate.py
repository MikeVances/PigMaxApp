"""
Перенесено из MINMAXapp (backend/app/core/design_climate.py, 59390c7) по переписи
docs/porting/house_census.md.

Расчётные наружные условия площадки (ASHRAE Fundamentals, гл. 14) по часовым
данным реанализа ERA5 через Open-Meteo (пункт ASHRAE 6 отчёта 2026-10-18).

  Отопление, 99.6 %: температура, ниже которой бывает 0.4 % часов года — по ней
    проверяют мощность горелок и Qmin в мороз.
  Охлаждение, 0.4 %: сухой термометр, выше которого 0.4 % часов, и СРЕДНИЙ
    СОВПАДАЮЩИЙ мокрый термометр (MCWB) — по ним туннель и Qmax.
  Испарение, 0.4 %: мокрый термометр, выше которого 0.4 % часов, — предел
    панельного охлаждения (панель не охладит ниже T_wb).

Данные: Open-Meteo Historical Weather API (ERA5, Copernicus), лицензия данных
CC BY 4.0, бесплатный доступ — для НЕкоммерческого использования (open-meteo.com).
Запрос уходит только по действию пользователя; координаты — фермы, которые он ввёл.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Sequence

from .errors import CalcError
from .psychro import pressure_at_altitude_pa, wet_bulb_c

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
YEARS = (2015, 2024)   # 10 полных лет — как у ASHRAE (не менее 8–10 лет наблюдений)
SOURCE = "ERA5 via Open-Meteo (CC BY 4.0), 2015–2024"


@dataclass
class DesignConditions:
    heating_996_c: float       # 99.6 % — расчётная зимняя
    cooling_04_db_c: float     # 0.4 % — расчётная летняя по сухому
    cooling_04_mcwb_c: float   # средний совпадающий мокрый к ней
    cooling_04_rh_pct: float   # средняя совпадающая RH (для форм, где вводят RH)
    evap_04_wb_c: float        # 0.4 % по мокрому — предел панелей
    hours: int                 # сколько часов в выборке
    elevation_m: float         # высота точки реанализа, м
    source: str = SOURCE


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    # Source: core/design_climate.py -> _percentile
    """Перцентиль с линейной интерполяцией (q в долях: 0.004 → 0.4 %)."""
    if not sorted_values:
        raise CalcError("climate_unavailable")
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def design_conditions(temps: Sequence[float], rhs: Sequence[float], elevation_m: float = 0.0) -> DesignConditions:
    # Source: core/design_climate.py -> design_conditions
    """Расчётные условия по часовым T (°C) и RH (%); пропуски (None) отбрасываются."""
    pairs = [(t, rh) for t, rh in zip(temps, rhs) if t is not None and rh is not None]
    if len(pairs) < 24 * 365:
        raise CalcError("climate_unavailable")   # меньше года — перцентили 0.4 % бессмысленны
    p = pressure_at_altitude_pa(elevation_m)
    t_sorted = sorted(t for t, _ in pairs)
    heat = _percentile(t_sorted, 0.004)
    cool = _percentile(t_sorted, 0.996)
    # Совпадающие: часы в полосе ±1 °C вокруг расчётной сухой (бины ASHRAE)
    band = [(t, rh) for t, rh in pairs if abs(t - cool) <= 1.0] or [max(pairs)]
    mcwb = sum(wet_bulb_c(t, rh, p) for t, rh in band) / len(band)
    mcrh = sum(rh for _, rh in band) / len(band)
    wb_sorted = sorted(wet_bulb_c(t, rh, p) for t, rh in pairs)
    return DesignConditions(
        heating_996_c=round(heat, 1), cooling_04_db_c=round(cool, 1),
        cooling_04_mcwb_c=round(mcwb, 1), cooling_04_rh_pct=round(mcrh, 0),
        evap_04_wb_c=round(_percentile(wb_sorted, 0.996), 1),
        hours=len(pairs), elevation_m=round(elevation_m, 0),
    )


def fetch_hourly(lat: float, lon: float, timeout_s: float = 60.0) -> tuple[list, list, float]:
    # Source: core/design_climate.py -> fetch_hourly
    """Часовые T и RH за YEARS из Open-Meteo; (temps, rhs, высота точки)."""
    query = urllib.parse.urlencode({
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "start_date": f"{YEARS[0]}-01-01", "end_date": f"{YEARS[1]}-12-31",
        "hourly": "temperature_2m,relative_humidity_2m", "timezone": "UTC",
    })
    try:
        with urllib.request.urlopen(f"{ARCHIVE_URL}?{query}", timeout=timeout_s) as resp:
            data = json.loads(resp.read())
        hourly = data["hourly"]
        return hourly["temperature_2m"], hourly["relative_humidity_2m"], float(data.get("elevation") or 0.0)
    except Exception:
        raise CalcError("climate_unavailable")
