"""
Этап 8.3: калибровка UA по кривой остывания — маршруты интерфейса.
Механизм сверен с MINMAXapp (test_house_parity.py); здесь — что маршрут считает,
сохраняет под токеном и не пускает в расчёт неправдоподобную калибровку.
"""
from __future__ import annotations

import json

import pytest

from backend.tests.parity.probe_cooling import _taps
from backend.app.core import cooling_fit

# Корпус по умолчанию настроек: 100 × 20 × 2.4 = 4800 м³ — синтетика под него
GOOD = _taps(cooling_fit, 2500, 9000, 6e7, 4800, -5.0, 20.0)
POINTS = [{"t_s": t, "temp_c": c} for t, c in GOOD]


def test_calibrate_recovers_model_ua(client):
    j = client.post("/calc/calibrate-ua", json={"t_outside_c": -5, "points": POINTS}).json()
    assert j["ua_coefficient"] == pytest.approx(2500, rel=0.03)
    assert j["volume_m3"] == 4800.0 and j["warnings"] == []


def test_calibration_is_saved_with_points(client, settings_file):
    r = client.put("/settings", json={"calibration": {"t_outside_c": -5, "points": POINTS}})
    assert r.status_code == 200
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["ua_coefficient"] == pytest.approx(2500, rel=0.03)
    assert saved["calibration"]["method"] == "cooling_curve" and len(saved["calibration"]["points"]) == len(POINTS)


def test_short_test_is_refused(client, settings_file):
    # Те же отметки, но сжатые по времени до ~4 минут: calibration_too_short
    fast = [{"t_s": p["t_s"] / 20, "temp_c": p["temp_c"]} for p in POINTS]
    r = client.put("/settings", json={"calibration": {"t_outside_c": -5, "points": fast}})
    assert r.status_code == 422 and r.json()["detail"]["code"] in ("calibration_too_short", "calibration_implausible")
    assert not settings_file.exists()


def test_calibration_needs_write_token(client, monkeypatch):
    monkeypatch.setenv("WRITE_TOKEN", "t")
    assert client.put("/settings", json={"calibration": {"t_outside_c": -5, "points": POINTS}}).status_code == 401
    # Только расчёт — без токена
    assert client.post("/calc/calibrate-ua", json={"t_outside_c": -5, "points": POINTS}).status_code == 200


def test_bad_taps(client):
    r = client.post("/calc/calibrate-ua", json={"t_outside_c": -5, "points": POINTS[:3]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_few_taps"


def test_manual_ua_drops_old_calibration(client, settings_file):
    # UA по конструкции (страница инструментов) поверх калибровки: запись теста с
    # другим UA больше не относится к числу и не должна его «подтверждать»
    client.put("/settings", json={"calibration": {"t_outside_c": -5, "points": POINTS}})
    saved = client.put("/settings", json={"ua_coefficient": 777.0}).json()
    assert saved["ua_coefficient"] == 777.0 and saved["calibration"] is None
