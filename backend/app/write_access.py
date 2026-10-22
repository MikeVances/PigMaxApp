"""
Защита эндпоинтов, которые ИЗМЕНЯЮТ сохранённые данные: настройки корпуса и
отопителя (PUT /settings).

Перенесено из MINMAXapp (backend/app/write_access.py) без изменения логики.
Состояние у приложения одно на всех посетителей, а авторизации нет: на
публичной площадке (Render) любой мог перезаписать общие настройки.

  WRITE_TOKEN задан    → запись только с заголовком X-Write-Token: <токен>
  WRITE_TOKEN не задан → запись открыта (локальная разработка)

Расчёты (POST /calc/*) данные не меняют и токена не требуют.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException

WRITE_TOKEN_HEADER = "X-Write-Token"

# Код ошибки, по которому frontend понимает, что нужно спросить токен
WRITE_TOKEN_REQUIRED = "write_token_required"


def require_write_token(
    x_write_token: str | None = Header(default=None, alias=WRITE_TOKEN_HEADER),
) -> None:
    # Читаем env при каждом запросе, а не при импорте: токен можно сменить
    # в панели Render без правки кода, а тесты подменяют его через monkeypatch
    expected = os.getenv("WRITE_TOKEN", "")
    if not expected:
        return

    # compare_digest — сравнение за постоянное время, чтобы токен нельзя
    # было подобрать по времени ответа. Сравниваем байты: на str с не-ASCII
    # символами compare_digest бросает TypeError, и клиент получил бы 500
    if x_write_token is None or not hmac.compare_digest(
        x_write_token.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail=WRITE_TOKEN_REQUIRED)
