"""
Этап 7: интерфейс — переводы после чистки и выбор генетики.

Из языковых файлов убрано наследство MINMAXapp (≈ 420 ключей птичника); сторож
держит обратное: всё, что использует страница, переведено, а птичья справка не
вернулась.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
LANGS = ("en", "ru", "pl", "de", "fr")


def _keys(lang: str) -> set[str]:
    return set(re.findall(r"^\s+([a-z_0-9]+)\s*:", (STATIC / "i18n" / f"{lang}.js").read_text(encoding="utf-8"), re.M))


def _used() -> set[str]:
    text = "".join((STATIC / f).read_text(encoding="utf-8") for f in ("index.html", "feedback.js"))
    used = set(re.findall(r'data-i18n(?:-[a-z]+)?="([a-z_0-9]+)"', text))
    used |= set(re.findall(r"\bt\('([a-z_0-9]+)'", text))
    used |= set(re.findall(r'data-page-title="([a-z_0-9]+)"', text))
    return used


def test_every_used_key_is_translated_in_english():
    assert not _used() - _keys("en"), sorted(_used() - _keys("en"))


@pytest.mark.parametrize("lang", LANGS[1:])
def test_languages_have_the_same_keys(lang):
    assert _keys(lang) == _keys("en"), sorted(_keys(lang) ^ _keys("en"))


@pytest.mark.parametrize("lang", LANGS)
def test_poultry_help_is_gone(lang):
    keys = _keys(lang)
    assert not {"help_body_html", "cal_intro", "opt_gas_open"} & keys


def test_ventilation_warning_codes_have_texts(client):
    # Предупреждения, которые выдаёт /calc/ventilation, показываются текстом warn_<код>
    j = client.post("/calc/ventilation", json={"weight_kg": 160, "num_pigs": 100, "t_outside_c": 15,
                                               "rh_outside_pct": 95, "genetics": "klimaatplatform_2021"}).json()
    codes = {w["code"] for w in j["warnings"]}
    assert codes and all(f"warn_{c}" in _keys("en") for c in codes), codes


def test_ventilation_uses_chosen_genetics(client):
    # Голландский профиль, откорм 66.22 кг: начало вентиляции 20 °C → расчётная 18.5
    j = client.post("/calc/ventilation", json={"weight_kg": 66.22, "num_pigs": 100, "t_outside_c": -5,
                                               "genetics": "klimaatplatform_2021", "phase": "finisher"}).json()
    assert (j["setpoint_c"], j["target_temp_c"], j["setpoint_meaning"]) == (18.5, 20.0, "ventilation_start")
    assert {"code": "season_ignored_for_profile", "params": {"genetics": "klimaatplatform_2021"}} in j["assumptions"]
    assert j["day_post_weaning"] is None


def test_pic_stays_default_with_season(client):
    j = client.post("/calc/ventilation", json={"weight_kg": 34, "num_pigs": 100, "t_outside_c": -5,
                                               "season": "summer"}).json()
    assert (j["setpoint_c"], j["genetics"]) == (18.0, "pic_2019")


def test_language_switch_event_exists():
    # Блоки, собранные скриптом, перерисовываются по событию смены языка
    assert "langchange" in (STATIC / "i18n.js").read_text(encoding="utf-8")
    assert "addEventListener('langchange'" in (STATIC / "index.html").read_text(encoding="utf-8")
