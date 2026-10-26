"""
pig_settings.py — Persistent building and heater configuration (JSON file).
"""

import json
import re
import logging
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from .house_id import HOUSE_ID_PATTERN

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


class HouseConfig(BaseModel):
    """Свой корпус фермы: размеры, отопитель, UA и калибровка — у каждого свои."""
    building:       BuildingConfig = BuildingConfig()
    heater:         HeaterConfig   = HeaterConfig()
    ua_coefficient: float | None   = None   # W/K — по конструкции или калибровке
    # Калибровка по кривой остывания (core/cooling_fit): отметки хранятся, чтобы
    # пересчитать UA, если поменяются размеры корпуса. Лежит ВНУТРИ записи корпуса:
    # у MINMAXapp калибровку одного корпуса подставляли в расчёт любого (аудит
    # 2026-10-18), здесь применить её к чужому корпусу нельзя по устройству
    calibration:    dict | None    = None


# Корпус, который появляется сам: пустой файл или файл до этапа 9 (один корпус).
# Имя нейтральное — без языка: его видят пользователи всех пяти языков интерфейса
DEFAULT_HOUSE_ID = "1"
_LEGACY_HOUSE_FIELDS = ("building", "heater", "ua_coefficient", "calibration")


class PigSettings(BaseModel):
    houses: dict[str, HouseConfig] = Field(default_factory=lambda: {DEFAULT_HOUSE_ID: HouseConfig()})
    farm:   FarmConfig = FarmConfig()

    @model_validator(mode="before")
    @classmethod
    def _one_house_file(cls, data):
        # Файл до этапа 9: корпус лежал полями верхнего уровня. Становится корпусом
        # "1" — иначе обновление молча сбросило бы размеры и калибровку на ферме
        if isinstance(data, dict) and "houses" not in data:
            legacy = {k: data[k] for k in _LEGACY_HOUSE_FIELDS if k in data}
            data = {k: v for k, v in data.items() if k not in _LEGACY_HOUSE_FIELDS}
            data["houses"] = {DEFAULT_HOUSE_ID: legacy}
        return data

    @model_validator(mode="after")
    def _at_least_one_house(self):
        if not self.houses:
            self.houses = {DEFAULT_HOUSE_ID: HouseConfig()}
        # Имя корпуса выводится в интерфейсе — то же правило, что у маршрутов
        # (house_id MINMAXapp): из копии или руками в файле разметка не придёт
        bad = [h for h in self.houses if not re.fullmatch(HOUSE_ID_PATTERN, h)]
        if bad:
            raise ValueError(f"house id not allowed: {bad[0]!r}")
        return self

    def house_id(self, requested: Optional[str]) -> str:
        """Запрошенный корпус или первый: без параметра — как до этапа 9, один корпус."""
        return requested if requested is not None else next(iter(self.houses))


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
