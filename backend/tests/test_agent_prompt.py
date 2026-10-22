"""
Перенесено из MINMAXapp (backend/tests/test_agent_prompt.py, 59390c7).

Промт агента-расчётчика (docs/api/v1/agent/system-prompt.md) не должен отставать
от контракта API v1: агент без кода знает о калькуляторе только то, что в промте и
в схеме. Новый инструмент, о котором промт молчит, агент не будет вызывать к месту;
код ошибки, которого промт не объясняет, он перескажет без пользы для человека.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PROMPT = (ROOT / "docs/api/v1/agent/system-prompt.md").read_text(encoding="utf-8")
SPEC = yaml.safe_load((ROOT / "docs/api/v1/openapi.yaml").read_text(encoding="utf-8"))
ERRORS_MD = (ROOT / "docs/api/v1/errors.md").read_text(encoding="utf-8")
API_SRC = (ROOT / "backend/app/api_v1.py").read_text(encoding="utf-8")
CORE_SRC = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "backend/app/core").glob("*.py"))

# Коды, которые API пока не отдаёт
NOT_YET: set = set()


def _error_table_codes() -> set:
    section = ERRORS_MD.split("## Ошибки", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r'<a id="([a-z0-9_]+)"></a>', section))


def _prompt_error_codes() -> set:
    section = PROMPT.split("## 6. Ошибки API", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"^\| `([a-z0-9_]+)` \|", section, flags=re.M))


def test_prompt_names_every_operation():
    # Именно в таблице раздела 2: по ней агент выбирает инструмент; упоминание
    # мимоходом в другом разделе не говорит, КОГДА его вызывать
    section = PROMPT.split("## 2. Инструменты", 1)[1].split("\n## ", 1)[0]
    in_table = set(re.findall(r"^\| `([A-Za-z]+)` \|", section, flags=re.M))
    ops = {op["operationId"] for path in SPEC["paths"].values() for op in path.values()}
    assert in_table == ops, f"нет в таблице: {sorted(ops - in_table)}; лишние: {sorted(in_table - ops)}"


def test_prompt_error_codes_are_documented():
    unknown = _prompt_error_codes() - _error_table_codes()
    assert not unknown, f"промт объясняет коды, которых нет в errors.md: {sorted(unknown)}"


def test_prompt_explains_every_error_the_api_returns():
    # Код «отдаётся», если он стоит в коде API или ядра (CalcError / _problem)
    emitted = {c for c in _error_table_codes() - NOT_YET
               if f'"{c}"' in API_SRC or f'"{c}"' in CORE_SRC}
    missing = emitted - _prompt_error_codes()
    assert not missing, f"API отдаёт коды, которых промт не объясняет: {sorted(missing)}"


def test_prompt_keeps_the_anti_hallucination_rules():
    # Правила, ради которых промт написан: числа только из ответа и номер расчёта рядом
    assert "request_id" in PROMPT
    assert "Каждое расчётное число — только из ответа API" in PROMPT


def test_prompt_has_no_sample_results():
    # Пример с числами расчёта модель «вспомнит» вместо вызова, когда инструмент не
    # подключился, — в промте не должно быть готовых результатов в м³/ч или кВт
    body = PROMPT.split("---", 1)[1]
    assert not re.search(r"\d[\d\s]{2,}\s*(м³/ч|кВт)", body)
