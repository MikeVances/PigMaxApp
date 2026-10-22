"""
Перенесено из MINMAXapp (backend/app/core/tunnel_design.py, 59390c7) по переписи
docs/porting/house_census.md.

Давление туннеля и рабочая точка вентиляторов для проекта (API v1, этап 3b).

Зачем: осевой вентилятор даёт паспортный расход только при 0 Па. Станки,
панели и впуск создают сопротивление, и подбор «по 0 Па» недобирает вентиляторов
(замечание владельца 2026-10-19). Поэтому проект отдаёт давление, при котором
читать каталог, а с характеристикой вентилятора — число вентиляторов в рабочей точке.

Сопротивление корпуса — та же модель, что у вкладки туннеля (tunnel_speed_calc):
  • скоростной напор в сечении ½ρv² (у свинарника — полное сечение корпуса;
    свободное сечение между клетками было случаем птичника MINMAXapp);
  • потеря на впуске ½ρ(v_впуск / Cd)²: открытый торец Cd 0.90, через панели 0.62;
  • панель — по кривой производителя (SANHE, evap_cooling_calc);
  • потери по длине (перегородки станков, балки, кормушки) — источника коэффициента нет,
    0 с допущением tunnel_length_loss_not_included; замер их заменяет.
Давления — при стандартном воздухе 1.2 кг/м³ (AMCA 210): в нём даны и паспорта
вентиляторов, и таблицы панелей, так рабочая точка ищется в одних единицах.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .errors import CalcError
from .evap_cooling_calc import SANHE_PADS, _pad_pressure_pa, design_pad_velocity_ms
from .tunnel_speed_calc import CD_INLET, CD_INLET_OPEN, RHO_STD_AIR

# Выше ~50 Па осевые вентиляторы свинарников теряют большую часть расхода —
# повод перепроверить каталог и площадь панелей (находка tunnel_pressure_high)
HIGH_TUNNEL_PRESSURE_PA = 50.0


@dataclass(frozen=True)
class Pads:
    pad_type: str = "7090"
    thickness_mm: int = 150


@dataclass(frozen=True)
class TunnelPressure:
    total: float
    section: float
    inlet: float
    pad: float
    length_loss: float
    basis: str                       # estimated | measured


@dataclass(frozen=True)
class FanSelection:
    count: int
    flow_per_fan_m3h: float
    operating_pressure_pa: float
    extrapolated: bool               # рабочая точка за последней точкой каталога


def pad_area_m2(q_m3h: float, pads: Pads) -> float:
    # Source: core/tunnel_design.py -> pad_area_m2
    """Площадь панелей при проектной скорости UGA для толщины (1.78 м/с у 150 мм)."""
    return q_m3h / 3600.0 / design_pad_velocity_ms(pads.thickness_mm)


def _pad_table(pads: Pads) -> tuple[list[float], list[float]]:
    # Source: core/tunnel_design.py -> _pad_table
    data = SANHE_PADS.get(pads.pad_type)
    if data is None or pads.thickness_mm not in data["pressure_pa"]:
        raise CalcError("validation_failed", field="pads")
    return data["velocities"], data["pressure_pa"][pads.thickness_mm]


class Resistance:
    """Сопротивление корпуса P(Q), Па при Q в м³/ч."""

    def __init__(self, section_m2: float, pads: Optional[Pads], pad_area: Optional[float],
                 measured: Optional[tuple[float, float]] = None):
        # Source: core/tunnel_design.py -> Resistance.__init__
        self.section_m2 = section_m2
        self.pads = pads
        # Воздух входит через панели; без панелей — через открытый торец сечения
        self.inlet_area = pad_area if pads is not None else section_m2
        self.cd = CD_INLET if pads is not None else CD_INLET_OPEN
        self.table = _pad_table(pads) if pads is not None else None
        # Замер (Q, P): всё сопротивление считаем квадратичным, P = P_m·(Q/Q_m)²
        self.measured = measured

    def parts(self, q_m3h: float) -> TunnelPressure:
        # Source: core/tunnel_design.py -> Resistance.parts
        q = q_m3h / 3600.0
        section = 0.5 * RHO_STD_AIR * (q / self.section_m2) ** 2
        inlet = 0.5 * RHO_STD_AIR * (q / (self.inlet_area * self.cd)) ** 2
        pad = _pad_pressure_pa(*self.table, q / self.inlet_area) if self.table else 0.0
        if self.measured is not None:
            q_m, p_m = self.measured
            return TunnelPressure(total=p_m * (q_m3h / q_m) ** 2, section=section, inlet=inlet,
                                  pad=pad, length_loss=0.0, basis="measured")
        return TunnelPressure(total=section + inlet + pad, section=section, inlet=inlet,
                              pad=pad, length_loss=0.0, basis="estimated")

    def __call__(self, q_m3h: float) -> float:
        # Source: core/tunnel_design.py -> Resistance.__call__
        return self.parts(q_m3h).total


def check_fan_curve(points: tuple[tuple[float, float], ...]) -> None:
    # Source: core/tunnel_design.py -> check_fan_curve
    """Точки (давление, расход): давление растёт, расход падает — иначе это не вентилятор."""
    for i in range(1, len(points)):
        (p0, q0), (p1, q1) = points[i - 1], points[i]
        if not (p1 > p0 and q1 < q0):
            raise CalcError("fan_curve_invalid", index=i)


def fan_flow_m3h(points: tuple[tuple[float, float], ...], p_pa: float) -> tuple[float, bool]:
    # Source: core/tunnel_design.py -> fan_flow_m3h
    """
    Расход одного вентилятора при давлении p, м³/ч, линейно между точками каталога.
    За последней точкой — продолжение последнего отрезка (флаг True: экстраполяция),
    не ниже нуля.
    """
    if p_pa <= points[0][0]:
        return points[0][1], False
    for (p0, q0), (p1, q1) in zip(points, points[1:]):
        if p_pa <= p1:
            return q0 + (q1 - q0) * (p_pa - p0) / (p1 - p0), False
    (p0, q0), (p1, q1) = points[-2], points[-1]
    return max(0.0, q1 + (q1 - q0) / (p1 - p0) * (p_pa - p1)), True


def _operating_point(n: int, points, resistance: Resistance) -> tuple[float, bool]:
    # Source: core/tunnel_design.py -> _operating_point
    """
    Расход системы из n вентиляторов, м³/ч: n·q(P(Q)) = Q. g(Q) = n·q(P(Q)) − Q
    убывает (P растёт с Q, q падает с P), g(0) > 0 — бисекция.
    """
    lo, hi = 0.0, n * points[0][1]
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        q_fan, _ = fan_flow_m3h(points, resistance(mid))
        if n * q_fan - mid > 0:
            lo = mid
        else:
            hi = mid
    _, extrapolated = fan_flow_m3h(points, resistance(lo))
    return lo, extrapolated


def select_fans(points: tuple[tuple[float, float], ...], resistance: Resistance,
                target_m3h: float) -> FanSelection:
    # Source: core/tunnel_design.py -> select_fans
    """Наименьшее число вентиляторов, которое в рабочей точке даёт не меньше target."""
    check_fan_curve(points)
    # Нижняя оценка — по паспорту 0 Па; дальше добавляем по одному: рабочая точка
    # с каждым вентилятором сдвигается вверх по давлению
    n = max(1, int(target_m3h // points[0][1]))
    while True:
        q_sys, extrapolated = _operating_point(n, points, resistance)
        if q_sys >= target_m3h * 0.999:
            return FanSelection(count=n, flow_per_fan_m3h=q_sys / n,
                                operating_pressure_pa=resistance(q_sys), extrapolated=extrapolated)
        if n >= 1000:
            # Даже тысяча вентиляторов не продавит корпус — каталог не того класса
            raise CalcError("fan_curve_invalid", index=len(points) - 1)
        n += 1
