"""
pig_settings.py — Persistent building and heater configuration (JSON file).
"""

import json
import logging
import os
from pathlib import Path
from pydantic import BaseModel

log = logging.getLogger(__name__)

_DEFAULT_DATA_FILE = Path(__file__).parent.parent.parent / "data" / "pig_settings.json"


def settings_path() -> Path:
    # Путь читаем при каждом обращении, а не при импорте: тесты и площадки
    # подменяют его через PIG_SETTINGS_JSON, и рабочий файл никогда не трогается
    return Path(os.getenv("PIG_SETTINGS_JSON") or _DEFAULT_DATA_FILE)


class BuildingConfig(BaseModel):
    length_m:   float = 100.0
    width_m:    float = 20.0
    height_m:   float = 2.4

    @property
    def volume_m3(self) -> float:
        return self.length_m * self.width_m * self.height_m

    @property
    def floor_area_m2(self) -> float:
        return self.length_m * self.width_m


class HeaterConfig(BaseModel):
    total_power_kw: float = 200.0
    heater_type:    str   = "gas"   # gas | electric


class PigSettings(BaseModel):
    building:       BuildingConfig = BuildingConfig()
    heater:         HeaterConfig   = HeaterConfig()
    ua_coefficient: float | None   = None   # W/K — from calibration


def load_settings() -> PigSettings:
    path = settings_path()
    if path.exists():
        try:
            return PigSettings(**json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            # Испорченный файл не должен ронять интерфейс, но и молча
            # подменяться умолчаниями тоже: иначе расчёт тихо идёт по чужому
            # корпусу 100×20 м, и никто не узнаёт почему
            log.warning("pig settings unreadable, using defaults: %s", path, exc_info=True)
    return PigSettings()


def save_settings(s: PigSettings) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Пишем во временный файл рядом и подменяем: обрыв записи посередине
    # (рестарт площадки, полный диск) оставляет старый файл целым, а не обрубок
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(s.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)
