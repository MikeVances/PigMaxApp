"""
Пункт 5: настройки хозяйства, расчётный климат по координатам и резервная копия.

Каталог профилей и файл настроек — временные (conftest); Open-Meteo не вызывается:
fetch_hourly подменён синтетикой, иначе тест зависел бы от сети и чужой квоты.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from backend.app.core import design_climate
from backend.app.core.errors import CalcError

GDIR = Path(os.environ["PIG_GENETICS_DIR"])
PROFILE = {"name": "Своя ферма", "setpoint_meaning": "room_temperature", "source": "замеры",
           "nursery": {"temp_by_weight": [[7, 28], [30, 22]]},
           "finisher": {"temp_by_weight": [[30, 21], [130, 17]]},
           "growth_by_day": [[0, 7], [40, 30], [140, 130]]}
FARM = {"altitude_m": 250, "mortality_pct": 2.5, "default_genetics": "farm_a"}


@pytest.fixture
def fclient(client):
    shutil.rmtree(GDIR, ignore_errors=True)
    yield client
    shutil.rmtree(GDIR, ignore_errors=True)


def _synthetic(lat, lon, timeout_s=60.0):
    # Год с запасом: −20…+30 °C по часам, RH 60 %, высота точки 180 м
    temps = [-20 + 50 * (i % 1000) / 999 for i in range(9000)]
    return temps, [60.0] * len(temps), 180.0


def test_farm_default_must_exist(fclient):
    r = fclient.put("/settings", json={"farm": FARM})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "genetics_not_found"
    fclient.put("/genetics/farm_a", json=PROFILE)
    saved = fclient.put("/settings", json={"farm": FARM}).json()["farm"]
    assert (saved["default_genetics"], saved["altitude_m"], saved["mortality_pct"]) == ("farm_a", 250, 2.5)


def test_default_profile_cannot_be_deleted(fclient):
    fclient.put("/genetics/farm_a", json=PROFILE)
    fclient.put("/settings", json={"farm": FARM})
    r = fclient.delete("/genetics/farm_a")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "profile_is_default"
    fclient.put("/settings", json={"farm": {**FARM, "default_genetics": "pic_2019"}})
    assert fclient.delete("/genetics/farm_a").status_code == 200
    # Встроенный и по умолчанию — первична причина «встроенный»
    assert fclient.delete("/genetics/pic_2019").json()["detail"]["code"] == "builtin_profile_read_only"


def test_design_climate_saved(fclient, monkeypatch):
    monkeypatch.setattr(design_climate, "fetch_hourly", _synthetic)
    farm = fclient.post("/settings/design-climate", json={"latitude": 55.75, "longitude": 37.62}).json()["farm"]
    expect = design_climate.design_conditions(*_synthetic(0, 0))
    assert (farm["latitude"], farm["longitude"]) == (55.75, 37.62)
    assert farm["winter_design_c"] == expect.heating_996_c and farm["summer_design_db_c"] == expect.cooling_04_db_c
    assert farm["summer_design_rh_pct"] == expect.cooling_04_rh_pct and farm["climate_source"] == expect.source
    assert farm["altitude_m"] == 180.0          # высоты не было — взята у точки реанализа


def test_design_climate_keeps_given_altitude(fclient, monkeypatch):
    monkeypatch.setattr(design_climate, "fetch_hourly", _synthetic)
    fclient.put("/settings", json={"farm": {"altitude_m": 420}})
    farm = fclient.post("/settings/design-climate", json={"latitude": 50, "longitude": 30}).json()["farm"]
    assert farm["altitude_m"] == 420


def test_design_climate_errors(fclient, monkeypatch):
    def down(*a, **k):
        raise CalcError("climate_unavailable")
    monkeypatch.setattr(design_climate, "fetch_hourly", down)
    r = fclient.post("/settings/design-climate", json={"latitude": 50, "longitude": 30})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "climate_unavailable"
    monkeypatch.setenv("WRITE_TOKEN", "t0ken")
    assert fclient.post("/settings/design-climate", json={"latitude": 50, "longitude": 30}).status_code == 401


def test_backup_round_trip(fclient, settings_file):
    fclient.put("/genetics/farm_a", json=PROFILE)
    fclient.put("/settings", json={"farm": FARM, "ua_coefficient": 512.0,
                                   "building": {"length_m": 72, "width_m": 15, "height_m": 2.8}})
    before = fclient.get("/settings").json()
    backup = fclient.get("/backup").json()
    assert set(backup["genetics_profiles"]) == {"farm_a"} and backup["genetics_profiles"]["farm_a"]["source"] == "замеры"
    # Ферма «переехала»: данных нет вовсе
    shutil.rmtree(GDIR); settings_file.unlink()
    assert fclient.get("/settings").json()["farm"]["default_genetics"] == "pic_2019"
    r = fclient.post("/backup", json=backup)
    assert r.json() == {"genetics_profiles": 1, "settings": True}
    assert fclient.get("/settings").json() == before
    assert fclient.get("/backup").json() == backup


def test_backup_all_or_nothing(fclient, settings_file):
    fclient.put("/settings", json={"ua_coefficient": 300.0})
    bad = {"settings": {"ua_coefficient": 999.0},
           "genetics_profiles": {"good": PROFILE,
                                 "bad": {**PROFILE, "growth_by_day": [[0, 7], [40, 30], [30, 50]]}}}
    r = fclient.post("/backup", json=bad)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "profile_growth_not_increasing"
    assert not GDIR.exists() or not any(GDIR.iterdir())
    assert fclient.get("/settings").json()["ua_coefficient"] == 300.0


@pytest.mark.parametrize("backup,code", [
    ({"settings": {"farm": {"default_genetics": "nowhere"}}}, "genetics_not_found"),
    ({"genetics_profiles": {"pic_2019": PROFILE}}, "builtin_profile_read_only"),
    ({"settings": {"building": {"length_m": "long"}}}, "backup_invalid"),
])
def test_backup_rejected(fclient, backup, code):
    r = fclient.post("/backup", json=backup)
    assert r.status_code == 422 and r.json()["detail"]["code"] == code


def test_backup_default_may_come_with_the_backup(fclient):
    # Профиль по умолчанию — из той же копии: проверка видит профили до записи
    r = fclient.post("/backup", json={"settings": {"farm": FARM}, "genetics_profiles": {"farm_a": PROFILE}})
    assert r.status_code == 200 and fclient.get("/settings").json()["farm"]["default_genetics"] == "farm_a"


def test_backup_restore_needs_token(fclient, monkeypatch):
    monkeypatch.setenv("WRITE_TOKEN", "t0ken")
    assert fclient.post("/backup", json={}).status_code == 401
    assert fclient.get("/backup").status_code == 200
