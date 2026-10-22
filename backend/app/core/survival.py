"""
# Source: MINMAXapp backend/app/core/survival.py (59390c7) — PORT survival_fraction 1:1

Доля живых животных на день цикла — одна формула для обоих приложений.

Модель: постоянный суточный падёж, подобранный так, чтобы к последнему дню
цикла выжило ровно (1 − total) от посадки:
    survival(day) = (1 − total) ^ ((day − 1) / (cycle_days − 1))
День 1 — день посадки (survival = 1). После конца цикла падёж продолжается
с тем же суточным темпом.

Мост к PigMaxApp: день цикла — день от начала периода проекта (start_day → 1),
cycle_days — длина периода (end_day − start_day + 1); дробный день допустим.
daily_mortality_to_total MINMAXapp не перенесена: это перевод старого формата
настроек птичника (падёж %/сутки), у PigMaxApp такого формата не было.
"""

from __future__ import annotations


def survival_fraction(day: float, total_mortality_pct: float, cycle_days: float) -> float:
    # Source: core/survival.py -> survival_fraction
    total = min(max(total_mortality_pct, 0.0), 100.0) / 100.0
    if total >= 1.0:
        return 0.0 if day > 1 else 1.0
    span = max(cycle_days - 1, 1)
    return (1.0 - total) ** (max(day - 1, 0) / span)
