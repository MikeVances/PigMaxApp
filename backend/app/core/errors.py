"""
# Source: MINMAXapp backend/app/core/errors.py (59390c7) — PORT 1:1
Ошибки расчётов и валидации, которые доходят до пользователя.

Сервер не знает языка интерфейса, поэтому текст ошибки он не формирует:
наружу уходит машинный код и параметры, а фразу на нужном языке подставляет
клиент из своего словаря (ключи ``srv_<code>`` в static/i18n/*.js).

Тот же приём, что для статусов инструментов (``status_code`` + параметры):
один контракт для всего API, без русских строк в Python.
"""
from __future__ import annotations

import json
from typing import Any, Dict


class CalcError(ValueError):
    """
    Ошибка входных данных расчёта с машинным кодом.

    ``code`` — ключ словаря клиента (без префикса ``srv_``), ``params`` —
    значения для подстановки в текст ({min}, {id}, {day} …).

    Наследует ValueError, чтобы существующие ``except ValueError`` в роутерах
    и валидаторах pydantic продолжали её ловить.
    """

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params: Dict[str, Any] = params
        super().__init__(self._message())

    def _message(self) -> str:
        # Source: core/errors.py -> CalcError._message
        # str(e) попадает в журнал сервера и в msg валидатора pydantic
        # («Value error, <code> {params}») — клиент разбирает именно этот формат
        if not self.params:
            return self.code
        return f"{self.code} {json.dumps(self.params, ensure_ascii=False, sort_keys=True)}"

    @property
    def detail(self) -> Dict[str, Any]:
        # Source: core/errors.py -> CalcError.detail
        """Тело ``detail`` для HTTPException: ``{"code": …, "params": {…}}``."""
        return {"code": self.code, "params": self.params}
