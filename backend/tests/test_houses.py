"""
Этап 9: корпуса фермы — у каждого свои размеры, отопитель, UA и калибровка.

Файл настроек — временный (conftest). Старый формат (один корпус полями верхнего
уровня) пишется в тот же временный файл: проверяем, что обновление его не сбросит.
"""
from __future__ import annotations

import json

from backend.tests.test_calibration import POINTS

OLD_FILE = {"building": {"length_m": 72, "width_m": 15, "height_m": 2.8},
            "heater": {"total_power_kw": 150, "heater_type": "electric"},
            "ua_coefficient": 512.0, "calibration": None,
            "farm": {"mortality_pct": 2.0, "altitude_m": 0, "default_genetics": "pic_2019"}}


def test_one_house_file_becomes_house_1(client, settings_file):
    # Файл до этапа 9 на ферме: размеры, отопитель и UA должны пережить обновление
    settings_file.write_text(json.dumps(OLD_FILE), encoding="utf-8")
    j = client.get("/settings").json()
    assert (j["house_id"], j["houses"]) == ("1", ["1"])
    assert (j["building"]["length_m"], j["heater"]["total_power_kw"], j["ua_coefficient"]) == (72, 150, 512.0)
    assert j["farm"]["mortality_pct"] == 2.0


def test_houses_are_independent(client):
    client.post("/houses", json={"house_id": "Корпус 2", "copy_from": "1"})
    client.put("/settings?house=1", json={"ua_coefficient": 600.0})
    client.put("/settings?house=Корпус 2", json={"ua_coefficient": 900.0,
                                                  "building": {"length_m": 50, "width_m": 12, "height_m": 2.5}})
    one, two = client.get("/settings?house=1").json(), client.get("/settings?house=Корпус 2").json()
    assert (one["ua_coefficient"], two["ua_coefficient"]) == (600.0, 900.0)
    assert (one["building"]["length_m"], two["building"]["length_m"]) == (100.0, 50.0)
    # Без параметра — первый корпус, как до этапа 9
    assert client.get("/settings").json()["house_id"] == "1"


def test_copy_takes_design_not_measurements(client):
    client.put("/settings", json={"building": {"length_m": 80, "width_m": 16, "height_m": 2.6},
                                  "calibration": {"t_outside_c": -5, "points": POINTS}})
    src = client.get("/settings").json()
    assert src["calibration"] is not None
    new = client.post("/houses", json={"house_id": "2", "copy_from": "1"}).json()
    assert new["building"] == src["building"] and new["heater"] == src["heater"]
    # UA и калибровка — измерения ТОГО корпуса: в новый не переходят
    assert new["ua_coefficient"] is None and new["calibration"] is None


def test_calibration_uses_own_house_volume(client):
    client.post("/houses", json={"house_id": "2"})
    client.put("/settings?house=2", json={"building": {"length_m": 50, "width_m": 20, "height_m": 2.4}})
    a = client.post("/calc/calibrate-ua", json={"t_outside_c": -5, "points": POINTS}).json()
    b = client.post("/calc/calibrate-ua?house=2", json={"t_outside_c": -5, "points": POINTS}).json()
    assert (a["volume_m3"], b["volume_m3"]) == (4800.0, 2400.0)


def test_rename_carries_calibration(client):
    client.put("/settings", json={"calibration": {"t_outside_c": -5, "points": POINTS}})
    client.post("/houses", json={"house_id": "2"})
    j = client.post("/houses/1/rename", json={"new_house_id": "Север"}).json()
    assert j["house_id"] == "Север" and j["calibration"] is not None
    # Порядок сохранён: переименованный первый остаётся корпусом по умолчанию
    assert j["houses"] == ["Север", "2"] and client.get("/settings").json()["house_id"] == "Север"


def test_house_errors(client):
    client.post("/houses", json={"house_id": "2"})
    assert client.post("/houses", json={"house_id": "2"}).json()["detail"]["code"] == "house_exists"
    assert client.post("/houses/1/rename", json={"new_house_id": "2"}).json()["detail"]["code"] == "house_exists"
    assert client.get("/settings?house=9").json()["detail"]["code"] == "house_not_found"
    assert client.put("/settings?house=9", json={"ua_coefficient": 1.0}).status_code == 404
    assert client.delete("/houses/2").json() == {"deleted": "2", "houses": ["1"]}
    r = client.delete("/houses/1")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "last_house"


def test_house_name_without_markup(client):
    assert client.post("/houses", json={"house_id": "<b>x</b>"}).status_code == 422
    bad = {"settings": {"houses": {"<script>": {}}}}
    assert client.post("/backup", json=bad).json()["detail"]["code"] == "backup_invalid"


def test_house_writes_need_token(client, monkeypatch):
    monkeypatch.setenv("WRITE_TOKEN", "t0ken")
    assert client.post("/houses", json={"house_id": "2"}).status_code == 401
    assert client.post("/houses/1/rename", json={"new_house_id": "3"}).status_code == 401
    assert client.delete("/houses/1").status_code == 401
