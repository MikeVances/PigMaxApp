import os
import json
import asyncio
import urllib.request
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class FeedbackPayload(BaseModel):
    message: str
    page: str = ""


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
async def send_feedback(payload: FeedbackPayload):
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise HTTPException(status_code=503, detail="Feedback not configured")

    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Empty message")

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
