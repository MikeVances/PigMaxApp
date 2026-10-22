"""
Контракт API v1: код и docs/api/v1/openapi.yaml не должны расходиться незаметно.

Механизм перенесён из MINMAXapp (backend/tests/test_api_v1_contract.py, 59390c7);
наборы запросов — свои, свинарники.

Контракт — то, на что опирается внешняя система; за этапы 3–4 расхождения
(поле в двух местах, несуществующий материал в примере, поля ответа без описания)
находились только перечитыванием схемы. Здесь это делает тест, в обе стороны:

  • запросы: каждый пример из спецификации проходит её схему И принимается сервером;
  • ответы: реальные ответы на набор характерных запросов проходят схему ответа
    для своего статуса — строго: поле, которого нет в контракте, — ошибка
    (unevaluatedProperties: false на каждом объекте);
  • маршруты: всё, что описано, существует, и всё, что существует, описано;
  • коды: каждый код находки, допущения и ошибки из ответов есть в errors.md.

OpenAPI 3.1 — это JSON Schema 2020-12, поэтому валидатор — jsonschema
(Draft202012Validator) с реестром referencing для ссылок #/components/...
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from backend.app import api_v1
from backend.app.core.errors import CalcError

ROOT = Path(__file__).resolve().parents[2]
SPEC = yaml.safe_load((ROOT / "docs/api/v1/openapi.yaml").read_text(encoding="utf-8"))
ERRORS_MD = (ROOT / "docs/api/v1/errors.md").read_text(encoding="utf-8")
SPEC_URI = "urn:pigmax:openapi-v1"

_COMBINATORS = ("allOf", "oneOf", "anyOf")


def _refs_in_combinators(node, found: set) -> set:
    """Компоненты, на которые ссылаются ветки allOf/oneOf/anyOf (ветки Envelope)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _COMBINATORS and isinstance(v, list):
                found |= {b["$ref"].rsplit("/", 1)[1] for b in v if isinstance(b, dict) and "$ref" in b}
            _refs_in_combinators(v, found)
    elif isinstance(node, list):
        for item in node:
            _refs_in_combinators(item, found)
    return found


def _strict(node, in_combinator: bool = False):
    """
    Копия спецификации, где каждый объект с properties закрыт: лишнее поле —
    ошибка. Ветки allOf/oneOf не закрываем по отдельности — их свойства
    оцениваются вместе; закрывается схема, внутри которой они стоят.
    """
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in _COMBINATORS and isinstance(v, list):
                out[k] = [_strict(item, in_combinator=True) for item in v]
            else:
                out[k] = _strict(v)
        has_shape = "properties" in out or any(k in out for k in _COMBINATORS)
        if has_shape and not in_combinator and "additionalProperties" not in out \
                and out.get("type", "object") == "object":
            out["unevaluatedProperties"] = False
        return out
    if isinstance(node, list):
        return [_strict(item) for item in node]
    return node


def _strict_spec(spec: dict) -> dict:
    spec = copy.deepcopy(spec)
    # Компонент, который служит веткой allOf, закрывать нельзя: закрытый, он не
    # увидит свойств соседней ветки (соседние свойства). Его закроет контейнер
    open_parts = _refs_in_combinators(spec, set())
    schemas = spec["components"]["schemas"]
    for name, schema in schemas.items():
        schemas[name] = _strict(schema, in_combinator=name in open_parts)
    spec["paths"] = _strict(spec["paths"])
    return spec


_REGISTRY = Registry().with_resource(SPEC_URI, Resource.from_contents(
    _strict_spec(SPEC), default_specification=DRAFT202012))


def _validator(ref: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"{SPEC_URI}{ref}"}, registry=_REGISTRY,
                                format_checker=FormatChecker())


def _check(instance, ref: str) -> None:
    errors = sorted(_validator(ref).iter_errors(instance), key=lambda e: list(e.absolute_path))
    assert not errors, "\n".join(
        f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors[:10])


def _response_ref(path: str, method: str, status: int, content_type: str) -> str:
    """Ссылка на схему ответа из спецификации (через components/responses — тоже)."""
    resp = SPEC["paths"][path][method]["responses"][str(status)]
    if "$ref" in resp:
        resp = SPEC["components"]["responses"][resp["$ref"].rsplit("/", 1)[1]]
    media = "application/problem+json" if content_type.startswith("application/problem+json") \
        else "application/json"
    assert media in resp["content"], f"{method.upper()} {path} {status}: в контракте нет {media}"
    schema = resp["content"][media]["schema"]
    if "$ref" in schema:
        return schema["$ref"]
    # Встроенная схема (справочники): ссылка внутрь paths — с экранированием «/» (RFC 6901)
    escaped = path.replace("~", "~0").replace("/", "~1")
    return f"#/paths/{escaped}/{method}/responses/{status}/content/{media.replace('/', '~1')}/schema"


# ─── Документированные коды ───────────────────────────────────────────────────

def _doc_codes(md: str) -> set:
    """Коды из таблиц errors.md: первая ячейка строки, где есть `код` (у ошибок — вторая)."""
    codes = set()
    for line in md.splitlines():
        if not line.startswith("|"):
            continue
        for cell in line.split("|")[1:]:
            m = re.search(r"`([a-z0-9_]+)`", cell)
            if m:
                codes.add(m.group(1))
                break
    return codes


_DOC_CODES = _doc_codes(ERRORS_MD)


def _emitted_codes(body: dict) -> set:
    codes = set()
    if "code" in body:
        codes.add(body["code"])
    for key in ("warnings", "findings", "assumptions"):
        codes |= {n["code"] for n in body.get(key, [])}
    return codes


# ─── Маршруты ─────────────────────────────────────────────────────────────────

def test_every_documented_route_exists_and_vice_versa():
    documented = {(p, m.upper()) for p, ops in SPEC["paths"].items() for m in ops}
    served = {(r.path, m) for r in api_v1.api.routes if hasattr(r, "methods")
              for m in r.methods if m not in ("HEAD", "OPTIONS")}
    # Служебные маршруты подприложения: живая схема, её страница и сам контракт
    served -= {("/openapi.json", "GET"), ("/docs", "GET"), ("/docs/oauth2-redirect", "GET"),
               ("/openapi.yaml", "GET")}
    assert documented == served


def test_contract_is_served(client):
    r = client.get("/api/v1/openapi.yaml")
    assert r.status_code == 200 and yaml.safe_load(r.text)["info"]["title"] == SPEC["info"]["title"]


# ─── Запросы: примеры из спецификации ─────────────────────────────────────────

def _request_examples():
    for path, ops in SPEC["paths"].items():
        for method, op in ops.items():
            content = op.get("requestBody", {}).get("content", {}).get("application/json", {})
            for name, ex in content.get("examples", {}).items():
                yield pytest.param(path, method, content["schema"]["$ref"], ex["value"],
                                   id=f"{method}{path}:{name}")


@pytest.mark.parametrize("path, method, ref, body", list(_request_examples()))
def test_spec_examples_are_valid_and_accepted(client, path, method, ref, body):
    _check(body, ref)
    r = client.request(method.upper(), f"/api/v1{path}", json=body)
    assert r.status_code == 201, r.text
    _check(r.json(), _response_ref(path, method, 201, r.headers["content-type"]))


# ─── Ответы: характерные запросы ──────────────────────────────────────────────

NOW_REQ = {"animals": {"group": "growing", "count": 1000, "genetics": "pic_2019", "day": 100},
           "conditions": {"t_outside_c": -10, "rh_outside_pct": 90}}
DESIGN_REQ = {
    "house": {"length_m": 60, "width_m": 18, "wall_height_m": 2.6, "ridge_height_m": 4.0,
              "envelope": {"walls": {"u_w_m2k": 0.25}, "roof": {"u_w_m2k": 0.22}}},
    "herd": {"genetics": "pic_2019", "count": 1000, "start_day": 30, "end_day": 150, "step_days": 30},
    "climate": {"winter_design_c": -25, "summer_design_db_c": 32, "summer_design_rh_pct": 40}}
CURVE = {"points": [{"pressure_pa": 0, "flow_m3h": 40_000}, {"pressure_pa": 50, "flow_m3h": 30_000}]}

CASES = [
    # (id, путь, тело)
    ("now-finisher", "/ventilation-requirements", NOW_REQ),
    ("now-weight-no-genetics", "/ventilation-requirements", {
        "animals": {"group": "growing", "count": 500, "weight_kg": 30}, "conditions": {"t_outside_c": 5, "rh_outside_pct": 95}}),
    ("now-wet-warm", "/ventilation-requirements", {**NOW_REQ, "conditions": {"t_outside_c": 14, "rh_outside_pct": 95}}),
    ("now-heating-speed", "/ventilation-requirements", {
        **NOW_REQ, "conditions": {"t_outside_c": -20, "rh_outside_pct": 85, "air_speed_ms": 0.5},
        "house": {"length_m": 60, "width_m": 18, "wall_height_m": 2.6,
                  "envelope": {"walls": {"layers": [{"material_id": "brick_exposed", "thickness_mm": 250}]},
                               "roof": {"u_w_m2k": 0.3}}, "floor": "straw"},
        "heaters": {"power_kw": 150, "type": "gas_open"}}),
    ("now-nl-nursery", "/ventilation-requirements", {
        "animals": {"group": "growing", "count": 800, "genetics": "klimaatplatform_2021", "day": 10},
        "conditions": {"t_outside_c": -5, "rh_outside_pct": 85}, "model": {"norm": "genetics", "max_norm": "genetics"}}),
    ("now-gestation-rd", "/ventilation-requirements", {
        "animals": {"group": "gestating_sow", "count": 500, "weight_kg": 220, "pregnancy_day": 80, "sow_profile": "rd_apk_2012"},
        "conditions": {"t_outside_c": -15, "rh_outside_pct": 85},
        "model": {"method": "rd_apk_2012", "norm": "rd_apk_2012", "co2_limit_ppm": 2000}}),
    ("now-farrowing", "/ventilation-requirements", {
        "animals": {"group": "lactating_sow", "count": 40, "weight_kg": 223, "lactation_day": 7, "sow_profile": "hypor_2023"},
        "conditions": {"t_outside_c": -15, "rh_outside_pct": 85}, "house": {"envelope": {"ua_w_per_k": 400}},
        "heaters": {"power_kw": 60, "type": "electric"}, "model": {"method": "stinn_xin_2014"}}),
    ("now-boar", "/ventilation-requirements", {
        "animals": {"group": "boar", "count": 20, "weight_kg": 280},
        "conditions": {"t_outside_c": 0, "rh_outside_pct": 80, "t_inside_c": 16}}),
    ("design-plain", "/ventilation-designs", DESIGN_REQ),
    ("design-pads-fans-layers", "/ventilation-designs", {
        **DESIGN_REQ, "house": {**DESIGN_REQ["house"], "altitude_m": 400,
                                "envelope": {"walls": {"layers": [{"material_id": "steel", "thickness_mm": 0.5},
                                                                  {"lambda_w_mk": 0.022, "thickness_mm": 100}]},
                                             "roof": {"u_w_m2k": 0.22}}},
        "pads": {"pad_type": "7090", "thickness_mm": 150}, "fan_curve": CURVE,
        "herd": {**DESIGN_REQ["herd"], "mortality_pct": 3}}),
    ("design-nl-measured-ua", "/ventilation-designs", {
        **DESIGN_REQ, "house": {**DESIGN_REQ["house"], "envelope": {"ua_w_per_k": 600}},
        "herd": {"genetics": "klimaatplatform_2021", "count": 600, "start_day": 43, "end_day": 140, "step_days": 20},
        "model": {"norm": "genetics", "max_norm": "genetics"}}),
    # Ошибки — тот же контракт: Problem
    ("err-genetics", "/ventilation-designs", {**DESIGN_REQ, "herd": {**DESIGN_REQ["herd"], "genetics": "nope"}}),
    ("err-no-growth-curve", "/ventilation-designs", {**DESIGN_REQ, "herd": {**DESIGN_REQ["herd"], "genetics": "rd_apk_2012"}}),
    ("err-field", "/ventilation-requirements", {**NOW_REQ, "animals": {**NOW_REQ["animals"], "count": 0}}),
    ("err-method", "/ventilation-requirements", {**NOW_REQ, "model": {"method": "stinn_xin_2014"}}),
    ("err-sow-profile", "/ventilation-requirements", {
        "animals": {"group": "dry_sow", "count": 10, "weight_kg": 200, "sow_profile": "nope"},
        "conditions": {"t_outside_c": 0, "rh_outside_pct": 80}}),
    ("err-fan-curve", "/ventilation-designs", {**DESIGN_REQ, "fan_curve": {"points": [
        {"pressure_pa": 0, "flow_m3h": 40_000}, {"pressure_pa": 25, "flow_m3h": 41_000}]}}),
    ("err-envelope", "/ventilation-designs", {**DESIGN_REQ, "house": {**DESIGN_REQ["house"], "envelope": {
        "walls": {"u_w_m2k": 9.5}, "roof": {"u_w_m2k": 9.5}}}}),
]


@pytest.mark.parametrize("path, body", [pytest.param(p, b, id=i) for i, p, b in CASES])
def test_responses_match_contract(client, path, body):
    r = client.post(f"/api/v1{path}", json=body)
    _check(r.json(), _response_ref(path, "post", r.status_code, r.headers["content-type"]))
    # Успешный ответ — только на запрос, который сам проходит контракт
    if r.status_code == 201:
        ref = SPEC["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        _check(body, ref)
    undocumented = _emitted_codes(r.json()) - _DOC_CODES
    assert not undocumented, f"коды без описания в errors.md: {sorted(undocumented)}"


def test_cases_cover_both_success_and_problem(client):
    # Проба доходит до проверяемого: без этого «все ответы валидны» могло бы
    # означать «все запросы упали с одной и той же ошибкой»
    statuses = {client.post(f"/api/v1{p}", json=b).status_code for _, p, b in CASES}
    assert {201, 404, 422} <= statuses


def test_bad_json_is_a_problem(client):
    r = client.post("/api/v1/ventilation-designs", content=b"{", headers={"content-type": "application/json"})
    _check(r.json(), _response_ref("/ventilation-designs", "post", 400, r.headers["content-type"]))


@pytest.mark.parametrize("path", ["/construction-materials", "/sources", "/genetics", "/sow-profiles",
                                  "/genetics/pic_2019", "/genetics/klimaatplatform_2021", "/genetics/danbred",
                                  "/sources/stinn_xin_2014"])
def test_catalogs_match_contract(client, path):
    r = client.get(f"/api/v1{path}")
    assert r.status_code == 200
    template = "/genetics/{profile_id}" if path.startswith("/genetics/") else \
        "/sources/{source_id}" if path.startswith("/sources/") else path
    _check(r.json(), _response_ref(template, "get", 200, r.headers["content-type"]))


def test_climate_matches_contract(client, monkeypatch):
    n = 24 * 366
    monkeypatch.setattr(api_v1.design_climate, "fetch_hourly",
                        lambda *a, **k: ([-20 + 60 * i / (n - 1) for i in range(n)], [50.0] * n, 150.0))
    api_v1._climate_cache.clear()
    r = client.get("/api/v1/climate-designs", params={"latitude": 45.0, "longitude": 39.0})
    _check(r.json(), _response_ref("/climate-designs", "get", 200, r.headers["content-type"]))

    def down(*a, **k):
        raise CalcError("climate_unavailable")
    monkeypatch.setattr(api_v1.design_climate, "fetch_hourly", down)
    api_v1._climate_cache.clear()
    r = client.get("/api/v1/climate-designs", params={"latitude": 10.0, "longitude": 10.0})
    _check(r.json(), _response_ref("/climate-designs", "get", 502, r.headers["content-type"]))
    api_v1._climate_cache.clear()


def test_every_emitted_code_is_documented():
    # Шире ответов набора: все коды, которые код умеет выдавать, — в errors.md
    import pathlib
    import re as _re
    app = pathlib.Path(api_v1.__file__).parent
    emitted = set()
    for f in app.rglob("*.py"):
        for m in _re.finditer(r'(?:_note\(|"code": |CalcError\(|_problem\(request, \d+, )"([a-z0-9_]+)"',
                              f.read_text(encoding="utf-8")):
            emitted.add(m.group(1))
    assert not emitted - _DOC_CODES, sorted(emitted - _DOC_CODES)


# ─── Сам валидатор: ловит ли он то, ради чего написан ─────────────────────────

def test_strict_validator_rejects_undocumented_field(client):
    # Проба доходит до проверяемого: строгий режим действительно отвергает лишнее
    body = client.post("/api/v1/ventilation-requirements", json=NOW_REQ).json()
    body["surprise_field"] = 1
    assert list(_validator("#/components/schemas/RequirementResult").iter_errors(body))
    body = client.post("/api/v1/ventilation-requirements", json=NOW_REQ).json()
    body["min_ventilation"]["surprise"] = 1
    assert list(_validator("#/components/schemas/RequirementResult").iter_errors(body))
    body = client.post("/api/v1/ventilation-requirements", json=NOW_REQ).json()
    del body["min_ventilation"]
    assert list(_validator("#/components/schemas/RequirementResult").iter_errors(body))
