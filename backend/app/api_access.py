"""
Перенесено из MINMAXapp (backend/app/api_access.py, 59390c7) — docs/porting/api_census.md.

Доступ к публичному API v1: ключи X-API-Key и лимит частоты.

Зачем: копия на Render публичная. Без ключа любой мог перебирать координаты в
/climate-designs — каждая новая точка это ~20 с запросов к Open-Meteo, и
бесплатная некоммерческая квота площадки кончилась бы на чужом скрипте.

  API_KEYS задан    → нужен заголовок X-API-Key с одним из ключей (через запятую);
                      у каждого ключа свои лимиты в минуту по группам маршрутов
  API_KEYS не задан → доступ открыт и без лимитов (локальная разработка и LAN),
                      как с WRITE_TOKEN

Без ключа всегда отдаётся документация (контракт, /docs): платформа агента
импортирует описание инструментов ДО того, как ей вписали ключ, и считать там нечего.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Optional

API_KEY_HEADER = "X-API-Key"
WINDOW_S = 60

# Лимиты в минуту на ключ (README API v1, «Доступ и лимиты»). Расчёт — секунды
# процессора; справочник — чтение из памяти; климат — внешний запрос к Open-Meteo
LIMITS = {"calc": 60, "catalog": 300, "climate": 10}

# Документация открыта без ключа — в ней нет ни расчёта, ни внешних запросов
PUBLIC_PATHS = {"/openapi.yaml", "/openapi.json", "/docs", "/docs/oauth2-redirect"}

CALC_PATHS = {"/ventilation-designs", "/ventilation-requirements"}
CLIMATE_PATHS = {"/climate-designs"}


def configured_keys() -> list[str]:
    # Читаем env при каждом запросе, а не при импорте: ключ отзывается правкой
    # окружения площадки и перезапуском, а тесты подменяют его через monkeypatch
    # Source: api_access.py -> configured_keys
    return [k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()]


def key_id(key: str) -> str:
    # Source: api_access.py -> key_id
    """Отпечаток ключа для журнала и счётчиков: сам ключ в журнал не попадает.

    8 hex-символов SHA-256 хватает, чтобы владелец отличил своих клиентов, и не
    хватает, чтобы по журналу восстановить ключ.
    """
    return "key-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def match_key(presented: Optional[str], keys: list[str]) -> Optional[str]:
    # Source: api_access.py -> match_key
    """Отпечаток совпавшего ключа или None.

    compare_digest — сравнение за постоянное время: ключ не подобрать по времени
    ответа. Сравниваем байты (на str с не-ASCII compare_digest бросает TypeError —
    клиент получил бы 500) и проходим ВСЕ ключи, не выходя на первом совпадении:
    иначе по времени было бы видно, какой по счёту ключ подошёл.
    """
    if presented is None:
        return None
    given = presented.encode("utf-8")
    found = None
    for k in keys:
        if hmac.compare_digest(given, k.encode("utf-8")):
            found = key_id(k)
    return found


def route_group(path: str) -> Optional[str]:
    # Source: api_access.py -> route_group
    """Группа лимита по пути внутри /api/v1; None — маршрут без ключа."""
    if path in PUBLIC_PATHS:
        return None
    if path in CALC_PATHS:
        return "calc"
    if path in CLIMATE_PATHS:
        return "climate"
    # Справочники и всё прочее, включая несуществующие пути: перебор адресов
    # без ключа не должен отличать «нет такого маршрута» от «нет ключа»
    return "catalog"


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    limit: int
    retry_after_s: int = 0


class RateLimiter:
    """Скользящее окно на (ключ, группа).

    Память ограничена сама: счётчики заводятся только для ключей из API_KEYS
    (их единицы), а не для адресов клиентов — в отличие от лимита обратной связи.
    """

    def __init__(self, limits: dict[str, int], window_s: int = WINDOW_S):
        # Source: api_access.py -> RateLimiter.__init__
        self.limits = limits
        self.window_s = window_s
        self._stamps: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, client: str, group: str, now: Optional[float] = None) -> Verdict:
        # Source: api_access.py -> RateLimiter.hit
        now = time.monotonic() if now is None else now
        limit = self.limits[group]
        with self._lock:
            stamps = self._stamps[(client, group)]
            while stamps and now - stamps[0] >= self.window_s:
                stamps.popleft()
            if len(stamps) >= limit:
                # Освободится место, когда из окна выйдет самая старая отметка.
                # Вверх и не меньше 1 с: «повторить через 0» клиент понял бы как «сразу».
                # Лимит 0 (группа закрыта) — окно пустое, ждать целое окно
                oldest = stamps[0] if stamps else now
                wait = max(1, math.ceil(self.window_s - (now - oldest)))
                return Verdict(False, limit, wait)
            stamps.append(now)
            return Verdict(True, limit)

    def reset(self) -> None:
        # Source: api_access.py -> RateLimiter.reset
        with self._lock:
            self._stamps.clear()


limiter = RateLimiter(LIMITS)
