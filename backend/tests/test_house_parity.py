"""
Паритет перенесённой физики корпуса с оригиналом MINMAXapp 59390c7.

Не тест «по смыслу», а сверка чисел: ограждение, расчётный климат, панели
(3 типа, рабочая точка и паспорт), подбор вентиляторов, горелки (CO₂, воздух,
загрузка — полная, частичная, без горелок, электрические, с полом обмена),
конденсат, психрометрия. 230+ значений — расхождение хоть в одном значит,
что перенос поменял поведение.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from backend.tests.parity.probe import run

GOLDEN = json.loads((Path(__file__).parent / "data" / "minmax_house_parity_59390c7.json").read_text(encoding="utf-8"))["values"]


def _diff(x, y, path=""):
    if isinstance(x, dict):
        for k in sorted(set(x) | set(y)):
            yield from _diff(x.get(k), y.get(k), f"{path}/{k}")
    elif isinstance(x, (list, tuple)):
        if len(x) != len(y):
            yield path, x, y
            return
        for i, (a, b) in enumerate(zip(x, y)):
            yield from _diff(a, b, f"{path}[{i}]")
    elif isinstance(x, float) and isinstance(y, (float, int)):
        if not math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-9):
            yield path, x, y
    elif x != y:
        yield path, x, y


def test_port_matches_minmax_original():
    port = json.loads(json.dumps(run(), default=str))
    diffs = list(_diff(GOLDEN, port))
    assert not diffs, diffs[:10]


def test_parity_covers_every_heater_branch():
    # Проба должна доходить до механизма: решатель горелок проверен во всех ветках
    duties = {k: v["duty_cycle"] for k, v in GOLDEN.items() if k.startswith("duty")}
    assert any(0 < d < 1 for d in duties.values()), "бисекция не проверена"
    assert any(d == 1.0 for d in duties.values()) and any(d == 0.0 for d in duties.values())
