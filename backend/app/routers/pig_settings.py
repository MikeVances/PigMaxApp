"""
pig_settings.py — GET/PUT endpoints for building and heater configuration.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from ..core.pig_settings import PigSettings, BuildingConfig, HeaterConfig, load_settings, save_settings

router = APIRouter()


class SettingsPayload(BaseModel):
    building:       BuildingConfig | None = None
    heater:         HeaterConfig   | None = None
    ua_coefficient: float | None         = None


@router.get("/settings")
def get_settings():
    s = load_settings()
    return {
        **s.model_dump(),
        "building": {
            **s.building.model_dump(),
            "volume_m3":    round(s.building.volume_m3, 1),
            "floor_area_m2": round(s.building.floor_area_m2, 1),
        }
    }


@router.put("/settings")
def put_settings(payload: SettingsPayload):
    s = load_settings()
    if payload.building is not None:
        s.building = payload.building
    if payload.heater is not None:
        s.heater = payload.heater
    if payload.ua_coefficient is not None:
        s.ua_coefficient = payload.ua_coefficient
    save_settings(s)
    return get_settings()
