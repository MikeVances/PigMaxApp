"""
pig_settings.py — Persistent building and heater configuration (JSON file).
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

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


class FarmConfig(BaseModel):
    """
    Настройки хозяйства — по образцу AppConfig MINMAXapp. Только заполняют формы
    интерфейса: API v1 без состояния и от них не зависит (иначе один и тот же
    запрос давал бы разный ответ на разных площадках).
    """
    # Высота фермы: на 1000 м воздух на 11 % реже — меньше массы в том же расходе
    altitude_m:       float = Field(0.0, ge=-100, le=4000)
    mortality_pct:    float = Field(3.0, ge=0, le=50)
    # Профиль, выбранный в формах по умолчанию; может быть и своим (genetics_store)
    default_genetics: str = Field("pic_2019", pattern=r"^[a-z0-9][a-z0-9_]{0,63}$")
    # Координаты и расчётный климат — пишет POST /settings/design-climate
    latitude:             Optional[float] = Field(None, ge=-90, le=90)
    longitude:            Optional[float] = Field(None, ge=-180, le=180)
    winter_design_c:      Optional[float] = Field(None, ge=-60, le=30)
    summer_design_db_c:   Optional[float] = Field(None, ge=0, le=55)
    summer_design_rh_pct: Optional[float] = Field(None, ge=1, le=100)
    summer_mcwb_c:        Optional[float] = Field(None, ge=-20, le=40)
    evap_wb_c:            Optional[float] = Field(None, ge=-20, le=40)
    climate_source:       Optional[str]   = Field(None, max_length=120)


class PigSettings(BaseModel):
    building:       BuildingConfig = BuildingConfig()
    heater:         HeaterConfig   = HeaterConfig()
    ua_coefficient: float | None   = None   # W/K — from calibration
    # Калибровка по кривой остывания (core/cooling_fit): отметки хранятся, чтобы
    # пересчитать UA, если поменяются размеры корпуса
    calibration:    dict | None    = None
    farm:           FarmConfig     = FarmConfig()


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
