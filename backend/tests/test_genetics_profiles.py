"""
Редактор профилей генетики: свои профили рядом со встроенными (core/genetics_store).

Каталог профилей — временный (PIG_GENETICS_DIR в conftest); фикстура чистит его
перед каждым тестом, рабочий backend/data/ не трогается.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

GDIR = Path(os.environ["PIG_GENETICS_DIR"])
DESIGN = {"house": {"length_m": 60, "width_m": 18, "wall_height_m": 2.6, "envelope": {"ua_w_per_k": 600}},
          "herd": {"genetics": "pic_2019", "count": 1000, "start_day": 30, "end_day": 150, "step_days": 30},
          "climate": {"winter_design_c": -25, "summer_design_db_c": 32, "summer_design_rh_pct": 40}}
SIMPLE = {"name": "Своя ферма", "setpoint_meaning": "room_temperature", "source": "замеры 2026",
          "nursery": {"temp_by_weight": [[7, 28], [30, 22]]},
          "finisher": {"temp_by_weight": [[30, 21], [130, 17]]},
          "growth_by_day": [[0, 7], [40, 30], [140, 130]]}


@pytest.fixture
def gclient(client):
    shutil.rmtree(GDIR, ignore_errors=True)
    yield client
    shutil.rmtree(GDIR, ignore_errors=True)


def _as_input(profile: dict) -> dict:
    # Ответ GET без служебных полей — это и есть формат PUT
    body = {k: v for k, v in profile.items() if k not in ("id", "source_id", "citation", "assumptions", "builtin")}
    return {**body, "source": "копия PIC"}


def test_copy_of_builtin_computes_the_same(gclient):
    pic = gclient.get("/api/v1/genetics/pic_2019").json()
    r = gclient.put("/genetics/my_pic", json=_as_input(pic))
    assert r.status_code == 200, r.json()
    a = gclient.post("/api/v1/ventilation-designs", json=DESIGN).json()
    b = gclient.post("/api/v1/ventilation-designs", json={**DESIGN, "herd": {**DESIGN["herd"], "genetics": "my_pic"}}).json()
    assert a["days"] == b["days"] and a["equipment"] == b["equipment"]
    assert {"code": "user_profile", "params": {"id": "my_pic", "source": "копия PIC"}} in b["assumptions"]
    assert "user_profile" in [s["id"] for s in b["sources"]]


def test_custom_profile_listed_and_readable(gclient):
    assert gclient.put("/genetics/farm_a", json=SIMPLE).status_code == 200
    listed = {p["id"]: p for p in gclient.get("/api/v1/genetics").json()["data"]}
    assert listed["farm_a"]["builtin"] is False and listed["pic_2019"]["builtin"] is True
    assert {p["id"] for p in gclient.get("/genetics").json()} >= {"farm_a", "pic_2019"}
    one = gclient.get("/api/v1/genetics/farm_a")
    assert one.status_code == 200 and one.json()["finisher"]["temp_by_weight"] == [[30, 21], [130, 17]]
    # Профиль меняется редактором — час кеша показал бы старую версию
    assert one.headers["cache-control"] == "no-cache"


def test_phase_split_is_kept(gclient):
    gclient.put("/genetics/farm_a", json=SIMPLE)
    body = {"animals": {"group": "growing", "count": 100, "genetics": "farm_a", "weight_kg": 30},
            "conditions": {"t_outside_c": -10, "rh_outside_pct": 90}}
    nur = gclient.post("/api/v1/ventilation-requirements", json={**body, "animals": {**body["animals"], "phase": "nursery"}}).json()
    fin = gclient.post("/api/v1/ventilation-requirements", json={**body, "animals": {**body["animals"], "phase": "finisher"}}).json()
    assert (nur["setpoints"]["setpoint_c"], fin["setpoints"]["setpoint_c"]) == (22.0, 21.0)


def test_builtin_is_read_only(gclient):
    assert gclient.put("/genetics/pic_2019", json=SIMPLE).json()["detail"]["code"] == "builtin_profile_read_only"
    r = gclient.delete("/genetics/pic_2019")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "builtin_profile_read_only"


def test_write_needs_token(gclient, monkeypatch):
    monkeypatch.setenv("WRITE_TOKEN", "t0ken")
    assert gclient.put("/genetics/farm_a", json=SIMPLE).status_code == 401
    assert gclient.put("/genetics/farm_a", json=SIMPLE, headers={"X-Write-Token": "t0ken"}).status_code == 200
    assert gclient.delete("/genetics/farm_a").status_code == 401


@pytest.mark.parametrize("patch,code", [
    ({"nursery": {"temp_by_weight": [[30, 22], [7, 28]]}}, "profile_curve_not_increasing"),
    ({"nursery": {"temp_by_weight": [[7, 28], [7, 22]]}}, "profile_curve_not_increasing"),
    ({"growth_by_day": [[0, 7], [40, 30], [40, 50]]}, "profile_growth_not_increasing"),
    ({"growth_by_day": [[0, 7], [40, 30], [80, 25]]}, "profile_growth_not_increasing"),
    ({"nursery": {"temp_by_weight": [[7, 280], [30, 22]]}}, "profile_point_out_of_range"),
    ({"growth_by_day": [[0, 7], [40, 900]]}, "profile_point_out_of_range"),
])
def test_bad_points_rejected(gclient, patch, code):
    r = gclient.put("/genetics/farm_a", json={**SIMPLE, **patch})
    assert r.status_code == 422 and r.json()["detail"]["code"] == code
    assert not (GDIR / "farm_a.json").exists()


def test_unknown_field_and_bad_id_rejected(gclient):
    assert gclient.put("/genetics/farm_a", json={**SIMPLE, "builtin": True}).status_code == 422
    assert gclient.put("/genetics/Farm.A", json=SIMPLE).status_code == 422


def test_delete_then_calc_is_404(gclient):
    gclient.put("/genetics/farm_a", json=SIMPLE)
    assert gclient.delete("/genetics/farm_a").json() == {"deleted": "farm_a"}
    assert gclient.delete("/genetics/farm_a").status_code == 404
    r = gclient.post("/api/v1/ventilation-designs", json={**DESIGN, "herd": {**DESIGN["herd"], "genetics": "farm_a"}})
    assert r.status_code == 404 and r.json()["code"] == "genetics_not_found"


def test_broken_file_is_skipped(gclient):
    GDIR.mkdir(parents=True, exist_ok=True)
    (GDIR / "broken.json").write_text("{not json", encoding="utf-8")
    ids = [p["id"] for p in gclient.get("/api/v1/genetics").json()["data"]]
    assert "broken" not in ids and "pic_2019" in ids
    assert gclient.get("/api/v1/genetics/broken").status_code == 404
