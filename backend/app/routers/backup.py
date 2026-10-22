"""
Резервная копия данных фермы — по образцу MINMAXapp (routers/backup.py, 59390c7).

  GET  /backup — настройки (корпус, отопитель, UA и калибровка, хозяйство) и свои
                 профили генетики; встроенные — часть приложения, не копируются
  POST /backup — восстановление под токеном записи. Каждая часть проходит те же
                 модели и проверки, что обычное сохранение; сначала проверяется
                 ВСЁ, потом пишется — битый файл не оставит данные наполовину.

Зачем: площадка с эфемерным диском (Render) теряет backend/data/ при деплое, а
переезд фермы на другой сервер — это тот же перенос файла.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from ..api_v1 import genetics_body
from ..core import genetics as genetics_mod
from ..core import genetics_store
from ..core.errors import CalcError
from ..core.pig_settings import PigSettings, load_settings, save_settings
from ..write_access import require_write_token
from .genetics_profiles import ProfileIn, _ranges

router = APIRouter()

BACKUP_VERSION = 1
# Поля ответа GET /genetics/{id}, которых нет во входе PUT: их выдаёт сервер
_SERVER_FIELDS = ("id", "source_id", "citation", "assumptions", "builtin")


class Backup(BaseModel):
    version: int = Field(BACKUP_VERSION, ge=1, le=BACKUP_VERSION)
    settings: Dict[str, Any] | None = None
    genetics_profiles: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


@router.get("/backup")
def get_backup():
    profiles = {}
    for p in genetics_store.list_custom():
        body = {k: v for k, v in genetics_body(p).items() if k not in _SERVER_FIELDS}
        # Источник своего профиля живёт в заметке user_profile — вернуть его в поле
        body["source"] = p.assumptions[0]["params"]["source"]
        profiles[p.id] = body
    return Backup(settings=load_settings().model_dump(), genetics_profiles=profiles).model_dump()


@router.post("/backup", dependencies=[Depends(require_write_token)])
def restore_backup(backup: Backup):
    # 1. Проверить всё, ничего не записывая
    try:
        profiles = {pid: ProfileIn(**body) for pid, body in backup.genetics_profiles.items()}
        settings = PigSettings(**backup.settings) if backup.settings is not None else None
    except ValidationError as e:
        raise HTTPException(status_code=422, detail={"code": "backup_invalid",
                                                     "params": {"field": ".".join(map(str, e.errors()[0].get("loc", ())))}})
    try:
        for pid, body in profiles.items():
            if pid in genetics_mod.PROFILES:
                raise CalcError("builtin_profile_read_only", id=pid)
            genetics_store._check_id(pid)
            _ranges(body)
            genetics_store.validate(body.model_dump(exclude_none=True))
        if settings is not None:
            default = settings.farm.default_genetics
            if default not in genetics_mod.PROFILES and default not in profiles and genetics_store.load(default) is None:
                raise CalcError("genetics_not_found", id=default)
    except CalcError as e:
        raise HTTPException(status_code=422, detail=e.detail)
    # 2. Записать: профили — до настроек (профиль по умолчанию должен существовать)
    for pid, body in profiles.items():
        genetics_store.save(pid, body.model_dump(exclude_none=True))
    if settings is not None:
        save_settings(settings)
    return {"genetics_profiles": len(profiles), "settings": settings is not None}
