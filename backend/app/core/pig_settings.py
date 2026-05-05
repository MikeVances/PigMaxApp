"""
pig_settings.py — Persistent building and heater configuration (JSON file).
"""

import json
from pathlib import Path
from pydantic import BaseModel

_DATA_FILE = Path(__file__).parent.parent.parent / "data" / "pig_settings.json"


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
    if _DATA_FILE.exists():
        try:
            return PigSettings(**json.loads(_DATA_FILE.read_text()))
        except Exception:
            pass
    return PigSettings()


def save_settings(s: PigSettings) -> None:
    _DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DATA_FILE.write_text(s.model_dump_json(indent=2))
