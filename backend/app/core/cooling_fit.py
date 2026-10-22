"""
Перенесено из MINMAXapp (backend/app/core/cooling_fit.py, 59390c7) по переписи
docs/porting/house_census.md (дополнение этапа 8.3). Мост (ADAPT): давление площадки
явным p_pa вместо настроек хозяйства MINMAXapp.

Калибровка UA по КРИВОЙ остывания пустого корпуса: «живая кнопка» на каждом градусе.

Почему не одна точка
────────────────────
«Упало на 5 °C за t минут» даёт только постоянную времени τ = C/UA. Чтобы получить
UA, нужна теплоёмкость C — а её главная часть (пол, стены) неизвестна. Подстановка
теплоёмкости одного воздуха ошибалась в разы (на синтетике — в 4–10 раз).

Модель из двух узлов
────────────────────
    воздух:      C_a · dT_a/dt = −UA · (T_a − T_нар) − K · (T_a − T_к)
    конструкции: C_к · dT_к/dt =  K · (T_a − T_к)

  C_a — теплоёмкость воздуха (известна точно: объём × ρ × c_p)
  UA  — теплопотери корпуса наружу, Вт/°C (искомое)
  K   — теплообмен воздуха с полом и стенами, Вт/°C
  C_к — эффективная теплоёмкость конструкций, Дж/°C

Условие теста: корпус несколько часов держал температуру → в момент старта
T_к = T_a. Тогда начальный наклон кривой определяется только UA и известной C_a,
а последующее замедление — подпиткой теплом от конструкций (K, C_к). По 4–6
точкам «время, когда градусник показал очередной градус» подгоняются UA, K, C_к.

Ограничение, которое кривая НЕ выявляет: если пол холоднее воздуха (горелки только
что разогнали воздух), кривая ложится на модель так же хорошо, но UA завышен
(+40 % при разнице 2 °C, +100 % при 5 °C). Поэтому прогрев перед тестом — обязателен.

Решение системы аналитическое (линейная система 2×2 → сумма двух экспонент),
подгонка — Нелдер–Мид по логарифмам параметров (параметры положительны и
различаются на порядки). Внешних зависимостей нет.

Что кривая может НЕ определить (аудит 2026-10-18)
──────────────────────────────────────────────────
• UA. Его задаёт начальный наклон, а у корпуса с сильной связью воздуха с полом
  (большое K) быстрая фаза кончается раньше первой отметки: дальше видна лишь
  медленная мода, и модели с UA на 25 % больше или меньше ложатся на отметки
  одинаково (на синтетике — ошибка до −80 % без единого предупреждения).
  Проверка — профиль: UA фиксируется на ×0.8 и ×1.25, остальное подгоняется
  заново; если хоть одна такая модель не хуже шума нажатий — UA не определён.
• C_к. Если за тест конструкции почти не остыли, их теплоёмкость уходит в
  бесконечность (было «3.7·10¹⁸× воздуха»), а UA при этом верен. Это не повод
  браковать тест — только не показывать число.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, List, Sequence, Tuple

from .building_thermal import AIR_CP_J_PER_KG_K, air_density_kg_m3
from .errors import CalcError
from .psychro import P_ATM_PA

MIN_TAPS = 4                 # точек после старта: меньше — параметры не разделяются
RECOMMENDED_DELTA_T = 15.0   # °C, минимальная разница с улицей для точности
FAST_TEST_S = 600            # быстрее 10 мин на всю кривую — подозрение на непрогретый корпус
POOR_FIT_RMS_C = 0.3         # °C, расхождение модели с отметками
# Профиль UA: насколько сдвинуть UA и что считать «так же хорошо».
# 0.02 °C — опоздание нажатия на ~3 с при типичном наклоне 1 °C за 2–3 мин
UA_PROFILE_FACTORS = (0.8, 1.25)
UA_PROFILE_NOISE_C = 0.02
# C_к не определена: конструкции почти не обменялись теплом с воздухом за тест
C_STRUCT_UNRESOLVED_RATIO = 1e3   # C_к / C_воздуха
K_UNRESOLVED_SHARE = 0.01         # K / UA


@dataclass
class CoolingFit:
    ua_w_per_k: float            # искомый коэффициент теплопотерь
    c_air_j_per_k: float         # теплоёмкость воздуха (по объёму)
    c_struct_j_per_k: float      # эффективная теплоёмкость конструкций
    k_struct_w_per_k: float      # теплообмен воздух ↔ конструкции
    rms_c: float                 # среднеквадратичное расхождение модели и отметок, °C
    duration_s: float
    warnings: List[str] = field(default_factory=list)
    # False — за тест конструкции почти не остыли, C_к из кривой не извлекается
    # (UA от этого не страдает): показывать «не определена», а не число
    c_struct_resolved: bool = True


# ─── Модель ──────────────────────────────────────────────────────────────────

def air_temperature_curve(
    ua: float, k: float, c_struct: float, c_air: float, delta_t0: float
) -> Callable[[float], float]:
    # Source: core/cooling_fit.py -> air_temperature_curve
    """T_a(t) − T_нар для старта из равновесия (T_к = T_a = T_нар + ΔT₀)."""
    a11 = -(ua + k) / c_air
    a12 = k / c_air
    a21 = k / c_struct
    a22 = -k / c_struct
    trace = a11 + a22
    det = a11 * a22 - a12 * a21
    disc = math.sqrt(max(trace * trace - 4.0 * det, 0.0))
    lam1 = (trace + disc) / 2.0          # медленная мода
    lam2 = (trace - disc) / 2.0          # быстрая мода
    slope0 = -ua * delta_t0 / c_air      # в равновесии теплообмен с конструкциями = 0
    if abs(lam2 - lam1) < 1e-15:
        return lambda t: delta_t0 * math.exp(lam1 * t)
    c2 = (slope0 - lam1 * delta_t0) / (lam2 - lam1)
    c1 = delta_t0 - c2
    return lambda t: c1 * math.exp(lam1 * t) + c2 * math.exp(lam2 * t)


# ─── Подгонка ────────────────────────────────────────────────────────────────

def _nelder_mead(fun: Callable[[List[float]], float], x0: List[float],
                 step: float = 0.5, max_iter: int = 3000) -> Tuple[List[float], float]:
    # Source: core/cooling_fit.py -> _nelder_mead
    n = len(x0)
    pts = [list(x0)] + [[x0[j] + (step if j == i else 0.0) for j in range(n)] for i in range(n)]
    vals = [fun(p) for p in pts]
    for _ in range(max_iter):
        order = sorted(range(n + 1), key=lambda i: vals[i])
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        if vals[-1] - vals[0] < 1e-14:
            break
        centroid = [sum(p[j] for p in pts[:-1]) / n for j in range(n)]
        refl = [centroid[j] + (centroid[j] - pts[-1][j]) for j in range(n)]
        f_refl = fun(refl)
        if f_refl < vals[0]:
            exp_ = [centroid[j] + 2.0 * (centroid[j] - pts[-1][j]) for j in range(n)]
            f_exp = fun(exp_)
            pts[-1], vals[-1] = (exp_, f_exp) if f_exp < f_refl else (refl, f_refl)
        elif f_refl < vals[-2]:
            pts[-1], vals[-1] = refl, f_refl
        else:
            contr = [centroid[j] + 0.5 * (pts[-1][j] - centroid[j]) for j in range(n)]
            f_contr = fun(contr)
            if f_contr < vals[-1]:
                pts[-1], vals[-1] = contr, f_contr
            else:
                pts = [pts[0]] + [[pts[0][j] + 0.5 * (p[j] - pts[0][j]) for j in range(n)] for p in pts[1:]]
                vals = [fun(p) for p in pts]
    best = min(range(n + 1), key=lambda i: vals[i])
    return pts[best], vals[best]


def fit_cooling_curve(
    volume_m3: float, t_outside_c: float, points: Sequence[Tuple[float, float]],
    p_pa: float = P_ATM_PA,
) -> CoolingFit:
    # Source: core/cooling_fit.py -> fit_cooling_curve
    """
    points — [(секунды от старта, температура внутри, °C)], первая точка — старт (0 с).

    Raises:
        ValueError: мало точек, время не растёт, температура не падает или не выше наружной
    """
    pts = sorted(points, key=lambda p: p[0])
    if len(pts) < MIN_TAPS + 1:
        raise CalcError("too_few_taps", min=MIN_TAPS)
    if pts[0][0] != 0:
        raise CalcError("first_tap_not_start")
    for (t0, temp0), (t1, temp1) in zip(pts, pts[1:]):
        if t1 <= t0:
            raise CalcError("taps_time_not_increasing")
        if temp1 >= temp0:
            raise CalcError("taps_temp_not_decreasing")
    if pts[-1][1] <= t_outside_c:
        raise CalcError("temp_not_above_outside")

    temp_start = pts[0][1]
    delta_t0 = temp_start - t_outside_c
    t_mean = (temp_start + pts[-1][1]) / 2.0
    c_air = volume_m3 * air_density_kg_m3(t_mean, p_pa) * AIR_CP_J_PER_KG_K

    taps = pts[1:]

    def sq_error(ua: float, k: float, c_struct: float) -> float:
        # Source: core/cooling_fit.py -> fit_cooling_curve.sq_error
        curve = air_temperature_curve(ua, k, c_struct, c_air, delta_t0)
        return sum((curve(t) - (temp - t_outside_c)) ** 2 for t, temp in taps)

    def objective(log_params: List[float]) -> float:
        # Source: core/cooling_fit.py -> fit_cooling_curve.objective
        return sq_error(*(math.exp(x) for x in log_params))

    # Начальное приближение: наклон первого отрезка при теплоёмкости одного воздуха.
    # Несколько стартов по K и C_к — функция цели не выпуклая
    t1, temp1 = taps[0]
    ua_guess = c_air * (temp_start - temp1) / t1 / max(delta_t0 - (temp_start - temp1) / 2.0, 0.1)
    best: Tuple[List[float], float] | None = None
    for k_factor in (1.0, 5.0, 20.0):
        for c_factor in (2.0, 10.0, 50.0):
            x0 = [math.log(ua_guess), math.log(k_factor * ua_guess), math.log(c_factor * c_air)]
            x, val = _nelder_mead(objective, x0)
            if best is None or val < best[1]:
                best = (x, val)

    assert best is not None
    ua, k, c_struct = (math.exp(x) for x in best[0])
    rms = math.sqrt(best[1] / len(taps))
    duration = taps[-1][0]

    warnings: List[str] = []
    if delta_t0 < RECOMMENDED_DELTA_T:
        warnings.append("small_delta_t")      # разница с улицей мала — точность падает
    if duration < FAST_TEST_S:
        warnings.append("fast_cooling")       # не прогрет корпус / открыты клапаны / работает вентиляция
    if rms > POOR_FIT_RMS_C:
        warnings.append("poor_fit")           # отметки не ложатся на модель — проверьте нажатия

    # Профиль UA: лучшая подгонка K и C_к при UA, сдвинутом на ±20–25 %.
    # Ложится не хуже шума нажатий — значит, отметки UA не различают
    tolerance = max(2.0 * rms, UA_PROFILE_NOISE_C)
    for factor in UA_PROFILE_FACTORS:
        ua_fixed = ua * factor
        best_sq = min(
            _nelder_mead(lambda lp: sq_error(ua_fixed, math.exp(lp[0]), math.exp(lp[1])),
                         [math.log(k_factor * ua_fixed), math.log(c_factor * c_air)])[1]
            for k_factor in (0.1, 1.0, 5.0, 20.0, 100.0)
            for c_factor in (2.0, 10.0, 50.0)
        )
        if math.sqrt(best_sq / len(taps)) <= tolerance:
            warnings.append("ua_not_resolved")
            break

    c_struct_resolved = (c_struct <= C_STRUCT_UNRESOLVED_RATIO * c_air
                         and k >= K_UNRESOLVED_SHARE * ua)

    return CoolingFit(
        ua_w_per_k=ua, c_air_j_per_k=c_air, c_struct_j_per_k=c_struct,
        k_struct_w_per_k=k, rms_c=rms, duration_s=duration, warnings=warnings,
        c_struct_resolved=c_struct_resolved,
    )
