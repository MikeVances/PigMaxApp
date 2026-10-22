import os
import json
import asyncio
import logging
import threading
import time
import urllib.request
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()
logger = logging.getLogger(__name__)

# Лимиты перенесены из MINMAXapp (backend/app/routers/feedback.py).
# Копия на Render публичная: без лимита форму можно использовать для спама
# в Telegram-чат владельца — каждое сообщение уходит туда напрямую.

# Telegram принимает до 4096 символов; 2000 с запасом на заголовок и адрес страницы
MAX_MESSAGE_LEN = 2000

# Не больше RATE_LIMIT сообщений за RATE_WINDOW_S с одного адреса
RATE_LIMIT = 5
RATE_WINDOW_S = 600

_recent: dict[str, deque] = defaultdict(deque)
_recent_lock = threading.Lock()

# Больше адресов в памяти не держим: при переполнении выбрасываем тех, чьё окно
# уже истекло. Иначе поток запросов с разных адресов растил бы словарь без предела
MAX_TRACKED_CLIENTS = 10_000

# Длина цепочки X-Forwarded-For пишется в журнал один раз — по ней на живой
# площадке видно, сколько узлов дописывает прокси (сами адреса не пишем)
_chain_logged = False


class FeedbackPayload(BaseModel):
    message: str = Field(..., max_length=MAX_MESSAGE_LEN)
    page: str = Field("", max_length=200)


def _trusted_hops() -> int:
    """Сколько записей в конце X-Forwarded-For дописали НАШИ прокси (env TRUSTED_PROXY_HOPS).

    0 (по умолчанию) — заголовок не учитывается: без известного прокси ему нельзя
    верить. Тогда все клиенты за прокси площадки делят один адрес и один лимит —
    жёстче, но спам по-прежнему не проходит.
    """
    try:
        return max(int(os.getenv("TRUSTED_PROXY_HOPS", "0")), 0)
    except ValueError:
        return 0


def _client_key(request: Request) -> str:
    # Прокси ДОПИСЫВАЕТ адрес того, кто к нему пришёл, в конец X-Forwarded-For и
    # не очищает то, что прислал клиент. Первая запись — что угодно, что клиент
    # написал сам: с ней лимит обходился бы подстановкой нового адреса в каждом
    # запросе. Верить можно только записи на позиции hops с конца
    global _chain_logged
    peer = request.client.host if request.client else "unknown"
    hops = _trusted_hops()
    forwarded = request.headers.get("x-forwarded-for", "")
    if not hops or not forwarded:
        return peer
    chain = [part.strip() for part in forwarded.split(",") if part.strip()]
    if not _chain_logged:
        logger.info("feedback: X-Forwarded-For из %d записей, TRUSTED_PROXY_HOPS=%d", len(chain), hops)
        _chain_logged = True
    return chain[-hops] if len(chain) >= hops else peer


def _allow(key: str, now: float) -> bool:
    """Скользящее окно: старые отметки выбрасываются, новая добавляется, если есть место."""
    with _recent_lock:
        if len(_recent) >= MAX_TRACKED_CLIENTS and key not in _recent:
            for stale in [k for k, s in _recent.items() if not s or now - s[-1] > RATE_WINDOW_S]:
                del _recent[stale]
            if len(_recent) >= MAX_TRACKED_CLIENTS:
                return False   # все окна живые — новый адрес ждёт, а не растит память
        stamps = _recent[key]
        while stamps and now - stamps[0] > RATE_WINDOW_S:
            stamps.popleft()
        if len(stamps) >= RATE_LIMIT:
            return False
        stamps.append(now)
        return True


def _post_telegram(token: str, chat_id: str, text: str) -> int:
    """Синхронный HTTP POST к Telegram API (запускается в thread pool)."""
    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    # Без parse_mode: текст пишет посетитель, и одиночный «_» или «*» в нём
    # (имена файлов, «CO2_max») ломал разметку — Telegram отвечал 400, а
    # пользователь видел «доставка не удалась»
    body = json.dumps({"chat_id": chat_id, "text": text})
    req  = urllib.request.Request(
        url, data=body.encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status


@router.post("/feedback")
async def send_feedback(payload: FeedbackPayload, request: Request):
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise HTTPException(status_code=503, detail="Feedback not configured")

    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Empty message")

    # Лимит — после проверок, до отправки: пустое сообщение не тратит попытку,
    # а отказ по лимиту не доходит до Telegram
    if not _allow(_client_key(request), time.monotonic()):
        raise HTTPException(status_code=429, detail="feedback_rate_limited",
                            headers={"Retry-After": str(RATE_WINDOW_S)})

    # Название проекта в заголовке: код скопирован из MINMAXapp, и сообщения
    # приходили с чужим именем — нельзя было понять, из какого приложения отзыв
    text = "📬 PigMaxApp Feedback\n"
    if payload.page:
        text += f"📄 Page: {payload.page}\n"
    text += f"\n{payload.message}"

    try:
        status = await asyncio.to_thread(_post_telegram, token, chat_id, text)
    except Exception:
        raise HTTPException(status_code=502, detail="Telegram delivery failed")

    if status != 200:
        raise HTTPException(status_code=502, detail="Telegram delivery failed")

    return {"ok": True}
