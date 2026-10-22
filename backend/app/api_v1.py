"""
Публичный API v1 — инженерный калькулятор свинарников (контракт: docs/api/v1/openapi.yaml).

Инфраструктура перенесена из MINMAXapp (backend/app/api_v1.py, 59390c7) по
переписи docs/porting/api_census.md: формат ошибок RFC 7807, request_id в каждом
ответе, журнал вызовов, ключи X-API-Key и лимиты частоты, расчётный климат.

Отдельное FastAPI-подприложение, смонтированное в /api/v1, а не набор роутеров
основного: у v1 свой формат ошибок (application/problem+json), и его обработчики
не должны менять ответы, которые уже разбирает интерфейс.

Без состояния: ни один маршрут не читает настройки корпуса (/settings) — всё,
что влияет на расчёт, приходит в запросе.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from typing import Any, Literal, Optional

from fastapi import FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import api_access
from .core import construction
from .core import design_climate
from .core import genetics as genetics_mod
from .core import sources
from .core import sow_climate
from .core.build_info import APP_VERSION, build_info
from .core.errors import CalcError
from .core.paths import PROJECT_ROOT, data_path
from .core import pig_design
from .core.house_checks import Airtightness
from .core import pig_requirement
from .core.max_ventilation import DEFAULT_RISE_C
from .core.min_ventilation import DEFAULT_CO2_LIMIT_PPM, DEFAULT_CO2_OUTDOOR_PPM
from .core.pig_heat import DEFAULT_FEED_LEVEL, MethodNotApplicable
from .core.tunnel_design import Pads

API_VERSION = "1.0.0"
ERRORS_DOC = "https://github.com/MikeVances/PigMaxApp/blob/main/docs/api/v1/errors.md"

api = FastAPI(title="PigMax Ventilation Engineering API", version=API_VERSION,
              docs_url="/docs", openapi_url="/openapi.json", redoc_url=None)


# ─── Идентификатор запроса и журнал вызовов ───────────────────────────────────
# Агент может пересказать правдоподобный «результат расчёта», которого не было
# (инструмент не подключился, а описание API он знает из промта) — без журнала
# это неотличимо от настоящего вызова. Поэтому у каждого ответа есть request_id
# (в теле: платформы агентов отдают модели тело, а не заголовки), агент обязан
# его называть, а сервер пишет каждый расчёт в журнал — сверить можно всегда.

AUDIT_LOG_PATH = data_path("API_AUDIT_LOG", "backend/data/api_audit.jsonl")
_audit_logger: Optional[logging.Logger] = None
_audit_lock = threading.Lock()


def _audit(entry: dict) -> None:
    # Source: api_v1.py -> _audit
    """Строка JSON в журнал вызовов. Сбой журнала не должен ломать расчёт."""
    global _audit_logger
    try:
        with _audit_lock:
            if _audit_logger is None:
                AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                logger = logging.getLogger("pigmax.api_v1.audit")
                logger.setLevel(logging.INFO)
                logger.propagate = False
                # 5 МБ × 3 файла: ~10 тыс. расчётов, диск площадки не забьётся
                handler = RotatingFileHandler(AUDIT_LOG_PATH, maxBytes=5_000_000, backupCount=3,
                                              encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(message)s"))
                logger.addHandler(handler)
                _audit_logger = logger
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **entry}
        # Чей ключ: владелец видит, какой клиент тратит лимит, — по отпечатку, не по ключу
        client = _client_var.get()
        if client:
            entry["client"] = client
        _audit_logger.info(json.dumps(entry, ensure_ascii=False, default=str))
    except Exception:
        logging.getLogger(__name__).warning("api audit log write failed", exc_info=True)


# Отпечаток ключа текущего запроса — для журнала. ContextVar, а не request.state:
# _audit вызывают и места, куда request не передаётся
_client_var: ContextVar[Optional[str]] = ContextVar("api_v1_client", default=None)


def _route_path(request: Request) -> str:
    # Source: api_v1.py -> _route_path
    """Путь внутри /api/v1: Starlette оставляет в scope полный путь и кладёт префикс в root_path."""
    path, root = request.scope["path"], request.scope.get("root_path", "")
    return path[len(root):] if root and path.startswith(root) else path


def _check_access(request: Request) -> Optional[Response]:
    # Source: api_v1.py -> _check_access
    """Отказ 401/429 или None — запрос идёт дальше (api_access: зачем и какие лимиты)."""
    group = api_access.route_group(_route_path(request))
    keys = api_access.configured_keys()
    if group is None or not keys:
        return None
    client = api_access.match_key(request.headers.get(api_access.API_KEY_HEADER), keys)
    if client is None:
        return _problem(request, 401, "api_key_required", "missing or invalid X-API-Key")
    _client_var.set(client)
    verdict = api_access.limiter.hit(client, group)
    if not verdict.allowed:
        return _problem(request, 429, "rate_limited", "rate limit exceeded",
                        {"limit": verdict.limit, "window_s": api_access.limiter.window_s},
                        headers={"Retry-After": str(verdict.retry_after_s)})
    return None


@api.middleware("http")
async def _request_id(request: Request, call_next):
    # Source: api_v1.py -> _request_id
    request.state.request_id = f"req-{uuid.uuid4().hex[:12]}"
    # Ключ проверяется здесь, а не зависимостью маршрута: зависимость FastAPI
    # срабатывает после разбора тела, и клиент без ключа получал бы 400/422 по
    # своим полям вместо 401. И у отказа уже есть request_id
    _client_var.set(None)
    denied = _check_access(request)
    response = denied if denied is not None else await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    return response


def _rid(request: Request) -> str:
    # Source: api_v1.py -> _rid
    return getattr(request.state, "request_id", f"req-{uuid.uuid4().hex[:12]}")


# ─── Ошибки: RFC 7807 ─────────────────────────────────────────────────────────

# HTTP-статус по коду ошибки ядра. Ссылка на несуществующий справочный объект — 404,
# всё остальное, что ядро отвергло по смыслу, — 422 (данные разобраны, но считать по
# ним нельзя)
_STATUS_BY_CODE = {"material_not_found": 404, "source_not_found": 404,
                   "genetics_not_found": 404, "sow_profile_not_found": 404,
                   "climate_unavailable": 502}


def _problem(request: Request, status: int, code: str, title: str, params: Optional[dict] = None,
             errors: Optional[list] = None, headers: Optional[dict] = None) -> JSONResponse:
    # Source: api_v1.py -> _problem
    rid = _rid(request)
    body: dict[str, Any] = {
        "type": f"{ERRORS_DOC}#{code}", "title": title, "status": status,
        "code": code, "instance": rid,
    }
    if params:
        body["params"] = params
    if errors:
        body["errors"] = errors
    # Отказ — тоже факт для сверки: агент мог сказать «посчитал», а сервер ответил ошибкой
    _audit({"request_id": rid, "path": request.url.path, "status": status, "code": code,
            "params": params, "errors": errors})
    return JSONResponse(body, status_code=status, media_type="application/problem+json", headers=headers)


@api.exception_handler(CalcError)
def _calc_error(request: Request, exc: CalcError) -> JSONResponse:
    # Source: api_v1.py -> _calc_error
    return _problem(request, _STATUS_BY_CODE.get(exc.code, 422), exc.code, exc.code.replace("_", " "), exc.params)


@api.exception_handler(MethodNotApplicable)
def _method_not_applicable(request: Request, exc: MethodNotApplicable) -> JSONResponse:
    # Методика не покрывает группу (Brown-Brandl — только растущие, Stinn & Xin — только
    # свиноматки): считать чужой формулой молча нельзя
    return _problem(request, 422, "method_not_applicable", "heat method does not cover this group",
                    {"message": str(exc)})


@api.exception_handler(RequestValidationError)
def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Source: api_v1.py -> _validation
    errs = exc.errors()
    # Тело — не JSON: это не ошибка полей, а неразобранный запрос
    if any(e.get("type") == "json_invalid" for e in errs):
        return _problem(request, 400, "bad_request", "request body is not valid JSON")
    fields = [{
        # loc начинается с "body" — клиенту нужен путь внутри своего JSON
        "field": ".".join(str(p) for p in e["loc"] if p != "body"),
        "code": e["type"],
        "message": e["msg"],
    } for e in errs]
    return _problem(request, 422, "validation_failed", "request fields are invalid", errors=fields)


# ─── Модели запроса ───────────────────────────────────────────────────────────
# Повторяют компоненты openapi.yaml. extra="forbid": опечатка в имени поля
# (wall_heigth_m) — 422, а не молча подставленное значение по умолчанию.

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LayerIn(_Strict):
    thickness_mm: float = Field(..., gt=0, le=1000)
    material_id: Optional[str] = Field(None, max_length=64)
    lambda_w_mk: Optional[float] = Field(None, ge=0.01, le=200)

    @model_validator(mode="after")
    def _one_lambda_source(self):
        # Source: api_v1.py -> LayerIn._one_lambda_source
        if (self.material_id is None) == (self.lambda_w_mk is None):
            raise ValueError("exactly one of material_id or lambda_w_mk is required")
        return self


class SurfaceIn(_Strict):
    layers: Optional[list[LayerIn]] = Field(None, min_length=1, max_length=6)
    u_w_m2k: Optional[float] = Field(None, ge=0.05, le=10)

    @model_validator(mode="after")
    def _one_of(self):
        # Source: api_v1.py -> SurfaceIn._one_of
        if (self.layers is None) == (self.u_w_m2k is None):
            raise ValueError("exactly one of layers or u_w_m2k is required")
        return self

    def to_core(self) -> construction.Surface:
        # Source: api_v1.py -> SurfaceIn.to_core
        if self.u_w_m2k is not None:
            return construction.Surface(u_w_m2k=self.u_w_m2k)
        return construction.Surface(layers=tuple(
            construction.Layer(l.thickness_mm, l.material_id, l.lambda_w_mk) for l in self.layers))


class EnvelopeIn(_Strict):
    ua_w_per_k: Optional[float] = Field(None, gt=0, le=200_000)
    walls: Optional[SurfaceIn] = None
    roof: Optional[SurfaceIn] = None
    end_walls: Optional[SurfaceIn] = None

    @model_validator(mode="after")
    def _one_of(self):
        # Source: api_v1.py -> EnvelopeIn._one_of
        by_construction = self.walls is not None or self.roof is not None or self.end_walls is not None
        if self.ua_w_per_k is not None and by_construction:
            raise ValueError("either ua_w_per_k or construction (walls, roof), not both")
        if self.ua_w_per_k is None and (self.walls is None or self.roof is None):
            raise ValueError("ua_w_per_k or both walls and roof are required")
        return self


class AirtightnessIn(_Strict):
    # Source: api_v1.py -> AirtightnessIn
    # «class» — ключевое слово Python: поле называется иначе, в JSON — class
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    tightness_class: Optional[Literal["tight", "moderate", "leaky", "very_leaky"]] = Field(None, alias="class")
    relative_leakage: Optional[float] = Field(None, gt=0, le=5)
    leak_at_4pa_m3h: Optional[float] = Field(None, gt=0)

    @model_validator(mode="after")
    def _one_of(self):
        given = [v for v in (self.tightness_class, self.relative_leakage, self.leak_at_4pa_m3h) if v is not None]
        if len(given) != 1:
            raise ValueError("exactly one of class, relative_leakage, leak_at_4pa_m3h is required")
        return self

    def to_core(self) -> Airtightness:
        return Airtightness(self.tightness_class, self.relative_leakage, self.leak_at_4pa_m3h)


def _app_version() -> str:
    # Source: api_v1.py -> _app_version
    info = build_info()
    return f"{APP_VERSION} · {info.get('commit', 'unknown')}"


# ─── Справочники ──────────────────────────────────────────────────────────────

_CATALOG_CACHE = {"Cache-Control": "public, max-age=3600"}


@api.get("/construction-materials")
def list_construction_materials() -> JSONResponse:
    # Source: api_v1.py -> list_construction_materials
    data = []
    for m in construction.MATERIALS.values():
        item: dict[str, Any] = {"id": m.id, "name": m.name, "lambda_w_mk": m.lambda_w_mk,
                                "source_id": m.source_id}
        if m.density_kg_m3 is not None:
            item["density_kg_m3"] = m.density_kg_m3
        if m.typical_thickness_mm:
            item["typical_thickness_mm"] = list(m.typical_thickness_mm)
        data.append(item)
    return JSONResponse({"data": data}, headers=_CATALOG_CACHE)


# Справочник источников: по `source_id` из ответа расчёта агент получает выверенный
# пересказ правила, как калькулятор его применяет и где модель от источника отличается.
# Поиск по ключу, а не по полному тексту гайдов: второго канала чисел, мимо журнала
# расчётов, у агента появиться не должно
SOURCE_ID_PATTERN = r"^[a-z0-9][a-z0-9_]{0,63}$"


@api.get("/sources")
def list_sources() -> JSONResponse:
    # Source: api_v1.py -> list_sources
    return JSONResponse({"data": [sources.as_dict(s) for s in sources.CATALOG.values()]},
                        headers=_CATALOG_CACHE)


@api.get("/sources/{source_id}")
def get_source(source_id: str = Path(..., pattern=SOURCE_ID_PATTERN)) -> JSONResponse:
    # Source: api_v1.py -> get_source
    s = sources.get_source(source_id)
    if s is None:
        raise CalcError("source_not_found", id=source_id)
    return JSONResponse(sources.as_dict(s), headers=_CATALOG_CACHE)


PROFILE_ID_PATTERN = r"^[a-z0-9][a-z0-9_]{0,63}$"


@api.get("/genetics")
def list_genetics() -> JSONResponse:
    """Профили генетики растущих свиней: смысл уставки и какие данные в профиле есть."""
    return JSONResponse({"data": [{
        "id": p.id, "name": p.name, "source_id": p.source_id, "setpoint_meaning": p.setpoint_meaning,
        "has_growth_curve": p.growth is not None,
        "has_ventilation_norm": any(c.min_vent_by_weight for c in (p.nursery, p.finisher)),
        "nursery_max_kg": p.nursery_max_kg,
    } for p in genetics_mod.PROFILES.values()]}, headers=_CATALOG_CACHE)


@api.get("/genetics/{profile_id}")
def get_genetics(profile_id: str = Path(..., pattern=PROFILE_ID_PATTERN)) -> JSONResponse:
    """
    Профиль по точкам источника: уставка, нормы и RH по весу для каждой фазы и кривая
    роста. Агенту уставка нужна и без расчёта корпуса («какая температура при 60 кг»).
    """
    p = genetics_mod.PROFILES.get(profile_id)
    if p is None:
        raise CalcError("genetics_not_found", id=profile_id)

    def phase(c: genetics_mod.PhaseCurves) -> dict:
        out: dict[str, Any] = {"temp_by_weight": [list(x) for x in c.temp_by_weight]}
        for name in ("min_vent_by_weight", "max_vent_by_weight", "rh_by_weight"):
            curve = getattr(c, name)
            if curve:
                out[name] = [list(x) for x in curve]
        return out

    body: dict[str, Any] = {
        "id": p.id, "name": p.name, "source_id": p.source_id, "citation": sources.citation(p.source_id),
        "setpoint_meaning": p.setpoint_meaning, "nursery_max_kg": p.nursery_max_kg,
        "nursery": phase(p.nursery), "finisher": phase(p.finisher),
        "assumptions": list(p.assumptions),
    }
    if p.growth:
        body["growth_by_day"] = [list(x) for x in p.growth]
    if p.rh_max_pct is not None:
        body["rh_max_pct"] = p.rh_max_pct
    return JSONResponse(body, headers=_CATALOG_CACHE)


@api.get("/sow-profiles")
def list_sow_profiles() -> JSONResponse:
    return JSONResponse({"data": [{
        "id": p.id, "name": p.name, "source_id": p.source_id, "setpoint_meaning": p.setpoint_meaning,
        "has_dry_gestation": p.dry_c is not None, "has_nest": p.nest is not None,
        "has_ventilation_norm": p.vent_lactation_min is not None,
    } for p in sow_climate.SOW_PROFILES.values()]}, headers=_CATALOG_CACHE)


# Расчётный климат по точке: первый запрос ~20 с (10 лет часов ERA5), повтор — из
# памяти процесса. Ключ — координаты с шагом 0.1° (~11 км): ближе ERA5 (сетка 0.25°)
# всё равно не различает. Кэш живёт до перезапуска — данные за 2015–2024 не меняются
_climate_cache: dict[tuple[float, float], dict] = {}
_climate_lock = threading.Lock()


@api.get("/climate-designs")
def get_climate_design(request: Request, latitude: float = Query(..., ge=-90, le=90),
                       longitude: float = Query(..., ge=-180, le=180)) -> JSONResponse:
    # Source: api_v1.py -> get_climate_design
    key = (round(latitude, 1), round(longitude, 1))
    with _climate_lock:
        cached = _climate_cache.get(key)
    if cached is None:
        # Вне блокировки: 20 с сети не должны держать другие точки
        temps, rhs, elevation = design_climate.fetch_hourly(*key)
        c = design_climate.design_conditions(temps, rhs, elevation)
        cached = {
            "winter_design_c": c.heating_996_c, "summer_design_db_c": c.cooling_04_db_c,
            "summer_design_rh_pct": c.cooling_04_rh_pct, "summer_mcwb_c": c.cooling_04_mcwb_c,
            "evap_wb_c": c.evap_04_wb_c, "elevation_m": c.elevation_m,
            "source_id": "era5_open_meteo", "citation": sources.citation("era5_open_meteo"),
            "latitude": key[0], "longitude": key[1],
        }
        with _climate_lock:
            _climate_cache[key] = cached
    rid = _rid(request)
    _audit({"request_id": rid, "path": request.url.path, "status": 200,
            "request": {"latitude": latitude, "longitude": longitude},
            "result": {k: cached[k] for k in ("winter_design_c", "summer_design_db_c", "summer_design_rh_pct")}})
    # Температуры из этого ответа агент переносит в проект — им тоже нужен номер для
    # сверки. В кэше request_id нет: у каждого запроса свой
    return JSONResponse({"request_id": rid, **cached}, headers=_CATALOG_CACHE)


# Выверенный контракт — для платформ агентов: описания инструментов они берут из
# OpenAPI, а живая /openapi.json сгенерирована FastAPI из моделей — без русских
# описаний, примеров и смысла полей. Отдаём файл как есть, без разбора
CONTRACT_PATH = PROJECT_ROOT / "docs" / "api" / "v1" / "openapi.yaml"


@api.get("/openapi.yaml", include_in_schema=False)
def contract(request: Request) -> Response:
    # Source: api_v1.py -> contract
    try:
        text = CONTRACT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Площадка выложена без docs/ (копия только backend/). Голый 500 агент
        # читает как «калькулятор сломан» — отдаём разбираемый отказ; он же
        # попадает в журнал, и владелец площадки видит, что контракт не выложен
        return _problem(request, 404, "contract_not_found", "API contract file is not deployed")
    return PlainTextResponse(text, media_type="application/yaml", headers={"Cache-Control": "no-cache"})


# ─── Сценарии ─────────────────────────────────────────────────────────────────

Group = Literal["growing", "boar", "dry_sow", "gestating_sow", "lactating_sow"]
HeatMethod = Literal["cigr2002", "brown_brandl_2004", "rd_apk_2012", "stinn_xin_2014"]
FeedLevel = Literal["S", "NL700", "NL750", "NL800", "DK700", "DK800", "DK900"]
FloorFeed = Literal["dry_feed_dry_floor", "dry_feed_average_floor", "wet_feed_wet_floor"]
Floor = Literal["concrete_slats", "wood", "asphalt", "straw", "wet_concrete"]


class ModelIn(_Strict):
    """Модель расчёта: методика тепла и пределы — всё с умолчаниями по источникам."""
    method: HeatMethod = "cigr2002"
    feed_level: FeedLevel = DEFAULT_FEED_LEVEL
    floor_feed: FloorFeed = "dry_feed_dry_floor"
    rh_max_pct: Optional[float] = Field(None, gt=30, le=95)
    co2_limit_ppm: float = Field(DEFAULT_CO2_LIMIT_PPM, ge=1000, le=5000)
    co2_outdoor_ppm: float = Field(DEFAULT_CO2_OUTDOOR_PPM, ge=300, le=600)
    norm: Literal["pic", "rd_apk_2012", "genetics", "none"] = "pic"
    max_norm: Literal["pic", "genetics", "none"] = "pic"
    rise_c: float = Field(DEFAULT_RISE_C, gt=0, le=15)


class HeatersIn(_Strict):
    power_kw: float = Field(0.0, ge=0, le=10_000)
    type: Literal["gas_open", "electric"] = "gas_open"


class AnimalsNowIn(_Strict):
    group: Group
    count: int = Field(..., gt=0, le=100_000)
    weight_kg: Optional[float] = Field(None, gt=0, le=400)
    genetics: Optional[str] = Field(None, pattern=PROFILE_ID_PATTERN)
    day: Optional[float] = Field(None, ge=0, le=250)
    phase: Optional[Literal["nursery", "finisher"]] = None
    sow_profile: Optional[str] = Field(None, pattern=PROFILE_ID_PATTERN)
    pregnancy_day: Optional[float] = Field(None, ge=0, le=120)
    lactation_day: Optional[float] = Field(None, ge=-7, le=42)
    litter_mass_kg: Optional[float] = Field(None, ge=0, le=200)
    milk_kg_day: Optional[float] = Field(None, ge=0, le=20)
    covered_nest: bool = False

    @model_validator(mode="after")
    def _check(self):
        # Существование профилей проверяет маршрут: ссылка на несуществующий
        # справочный объект — 404, а внутри валидатора она стала бы 422 по полю
        if self.group == "growing" and self.weight_kg is None and (self.genetics is None or self.day is None):
            raise ValueError("growing pigs need weight_kg, or genetics with day")
        if self.group != "growing" and self.weight_kg is None:
            raise ValueError("weight_kg is required for sows and boars")
        if self.group == "gestating_sow" and self.pregnancy_day is None:
            raise ValueError("pregnancy_day is required for gestating_sow")
        if self.group == "lactating_sow" and self.lactation_day is None:
            raise ValueError("lactation_day is required for lactating_sow")
        return self


class ConditionsIn(_Strict):
    t_outside_c: float = Field(..., ge=-50, le=50)
    rh_outside_pct: float = Field(..., ge=0, le=100)
    t_inside_c: Optional[float] = Field(None, ge=5, le=40)
    air_speed_ms: Optional[float] = Field(None, ge=0, le=6)


class HouseNowIn(_Strict):
    length_m: Optional[float] = Field(None, gt=0, le=500)
    width_m: Optional[float] = Field(None, gt=0, le=100)
    wall_height_m: Optional[float] = Field(None, gt=0, le=20)
    ridge_height_m: Optional[float] = Field(None, gt=0, le=30)
    envelope: Optional[EnvelopeIn] = None
    airtightness: Optional[AirtightnessIn] = None
    floor: Floor = "concrete_slats"
    altitude_m: float = Field(0.0, ge=-100, le=4000)

    @model_validator(mode="after")
    def _geometry_for_construction(self):
        if self.envelope is not None and self.envelope.ua_w_per_k is None and None in (
                self.length_m, self.width_m, self.wall_height_m):
            raise ValueError("envelope by construction needs length_m, width_m and wall_height_m")
        return self


class RequirementRequest(_Strict):
    animals: AnimalsNowIn
    conditions: ConditionsIn
    house: HouseNowIn = HouseNowIn()
    heaters: Optional[HeatersIn] = None
    creep_heat_w_per_pen: float = Field(0.0, ge=0, le=2000)
    model: ModelIn = ModelIn()


def _geometry(house) -> Optional[construction.HouseGeometry]:
    if None in (house.length_m, house.width_m, house.wall_height_m):
        return None
    return construction.HouseGeometry(house.length_m, house.width_m, house.wall_height_m, house.ridge_height_m)


def _ua(house, envelope: Optional[EnvelopeIn]) -> tuple[Optional[float], Optional[construction.EnvelopeUA], list[str]]:
    """UA из запроса: заданный или по конструкции (с самой конструкцией — для конденсата)."""
    if envelope is None:
        return None, None, []
    if envelope.ua_w_per_k is not None:
        return envelope.ua_w_per_k, None, []
    e = construction.envelope_ua(_geometry(house), envelope.walls.to_core(), envelope.roof.to_core(),
                                 envelope.end_walls.to_core() if envelope.end_walls else None)
    return e.ua_w_per_k, e, ["br443_2019_s3", "br443_2019_s4_3"]


def _require_profiles(genetics: Optional[str], sow_profile: Optional[str]) -> None:
    if genetics is not None and genetics not in genetics_mod.PROFILES:
        raise CalcError("genetics_not_found", id=genetics)
    if sow_profile is not None and sow_profile not in sow_climate.SOW_PROFILES:
        raise CalcError("sow_profile_not_found", id=sow_profile)


def _sources(ids: list[str]) -> list[dict]:
    # Тот же справочник, что отдаёт /sources: по id агент получит пересказ и ограничения
    return [{"id": i, "citation": sources.citation(i)} for i in dict.fromkeys(ids) if sources.get_source(i)]


@api.post("/ventilation-requirements", status_code=201)
def create_ventilation_requirement(req: RequirementRequest, request: Request) -> JSONResponse:
    """Потребность сейчас: одна группа свиней в текущих условиях."""
    a, c, h, m = req.animals, req.conditions, req.house, req.model
    _require_profiles(a.genetics, a.sow_profile)
    ua, env_obj, env_sources = _ua(h, h.envelope)
    try:
        r = pig_requirement.requirement(pig_requirement.RequirementInput(
            group=a.group, count=a.count, t_outside_c=c.t_outside_c, rh_outside_pct=c.rh_outside_pct,
            weight_kg=a.weight_kg, genetics=a.genetics, day=a.day, phase=a.phase, sow_profile=a.sow_profile,
            pregnancy_day=a.pregnancy_day, lactation_day=a.lactation_day, litter_mass_kg=a.litter_mass_kg,
            milk_kg_day=a.milk_kg_day, covered_nest=a.covered_nest, t_inside_c=c.t_inside_c,
            method=m.method, feed_level=m.feed_level, floor_feed=m.floor_feed, rh_max_pct=m.rh_max_pct,
            co2_limit_ppm=m.co2_limit_ppm, co2_outdoor_ppm=m.co2_outdoor_ppm, norm=m.norm, max_norm=m.max_norm,
            rise_c=m.rise_c, altitude_m=h.altitude_m, ua_w_per_k=ua,
            heater_power_kw=req.heaters.power_kw if req.heaters else 0.0,
            heater_type=req.heaters.type if req.heaters else "gas_open",
            creep_heat_w_per_pen=req.creep_heat_w_per_pen, floor=h.floor, air_speed_ms=c.air_speed_ms,
            geometry=_geometry(h), envelope=env_obj,
            airtightness=h.airtightness.to_core() if h.airtightness else None))
    except MethodNotApplicable:
        raise
    except ValueError as e:
        if isinstance(e, CalcError):
            raise
        raise CalcError("invalid_parameters", message=str(e))
    rid = _rid(request)
    body: dict[str, Any] = {
        "request_id": rid, "app_version": _app_version(),
        "weight_kg": r.weight_kg, "t_inside_c": r.t_inside_c, "setpoints": r.setpoints, "heat": r.heat,
        "min_ventilation": r.min_ventilation, "max_ventilation": r.max_ventilation,
        "sources": _sources(r.sources + env_sources), "assumptions": r.assumptions, "warnings": r.warnings,
    }
    if r.heating is not None:
        body["heating"] = r.heating
    if r.effective_temperature is not None:
        body["effective_temperature"] = r.effective_temperature
    _audit({"request_id": rid, "path": request.url.path, "status": 201,
            "request": req.model_dump(exclude_none=True),
            "result": {"min_total_m3h": r.min_ventilation["total_m3h"], "basis": r.min_ventilation["basis"],
                       "t_inside_c": r.t_inside_c}})
    return JSONResponse(body, status_code=201)


class HouseIn(_Strict):
    length_m: float = Field(..., gt=0, le=500)
    width_m: float = Field(..., gt=0, le=100)
    wall_height_m: float = Field(..., gt=0, le=20)
    ridge_height_m: Optional[float] = Field(None, gt=0, le=30)
    envelope: EnvelopeIn
    airtightness: Optional[AirtightnessIn] = None
    altitude_m: float = Field(0.0, ge=-100, le=4000)


class HerdIn(_Strict):
    genetics: str = Field(..., pattern=PROFILE_ID_PATTERN)
    count: int = Field(..., gt=0, le=100_000)
    start_day: float = Field(..., ge=0, le=250)
    end_day: float = Field(..., ge=1, le=250)
    mortality_pct: float = Field(0.0, ge=0, le=50)
    step_days: int = Field(pig_design.DEFAULT_STEP_DAYS, ge=1, le=60)
    phase: Optional[Literal["nursery", "finisher"]] = None

    @model_validator(mode="after")
    def _check(self):
        if self.end_day <= self.start_day:
            raise ValueError("end_day must be after start_day")
        return self


class ClimateIn(_Strict):
    winter_design_c: float = Field(..., ge=-60, le=30)
    summer_design_db_c: float = Field(..., ge=0, le=55)
    summer_design_rh_pct: float = Field(..., ge=1, le=100)
    winter_rh_pct: float = Field(pig_design.DEFAULT_WINTER_RH_PCT, ge=1, le=100)


class PadsIn(_Strict):
    pad_type: Literal["7090", "7060", "5090"] = "7090"
    thickness_mm: int = 150


class FanPointIn(_Strict):
    pressure_pa: float = Field(..., ge=0, le=500)
    flow_m3h: float = Field(..., gt=0, le=200_000)


class FanCurveIn(_Strict):
    points: list[FanPointIn] = Field(..., min_length=2, max_length=12)


class DesignRequest(_Strict):
    house: HouseIn
    herd: HerdIn
    climate: ClimateIn
    model: ModelIn = ModelIn()
    heaters: Optional[HeatersIn] = None
    pads: Optional[PadsIn] = None
    fan_curve: Optional[FanCurveIn] = None
    summer_target_c: float = Field(pig_design.DEFAULT_SUMMER_TARGET_C, ge=15, le=35)


@api.post("/ventilation-designs", status_code=201)
def create_ventilation_design(req: DesignRequest, request: Request) -> JSONResponse:
    """Проект корпуса для цикла растущих свиней: по дням и подбор оборудования."""
    h, e, m = req.house, req.house.envelope, req.model
    _require_profiles(req.herd.genetics, None)
    ua, _ = (e.ua_w_per_k, None) if e.ua_w_per_k is not None else (None, None)
    try:
        d = pig_design.design(pig_design.DesignInput(
            genetics=req.herd.genetics, count=req.herd.count, start_day=req.herd.start_day,
            end_day=req.herd.end_day, length_m=h.length_m, width_m=h.width_m, wall_height_m=h.wall_height_m,
            ridge_height_m=h.ridge_height_m, ua_w_per_k=ua,
            walls=e.walls.to_core() if e.walls else None, roof=e.roof.to_core() if e.roof else None,
            end_walls=e.end_walls.to_core() if e.end_walls else None,
            winter_design_c=req.climate.winter_design_c, summer_design_db_c=req.climate.summer_design_db_c,
            summer_design_rh_pct=req.climate.summer_design_rh_pct, winter_rh_pct=req.climate.winter_rh_pct,
            mortality_pct=req.herd.mortality_pct, step_days=req.herd.step_days, phase=req.herd.phase,
            method=m.method, feed_level=m.feed_level, floor_feed=m.floor_feed, rh_max_pct=m.rh_max_pct,
            co2_limit_ppm=m.co2_limit_ppm, co2_outdoor_ppm=m.co2_outdoor_ppm, norm=m.norm, max_norm=m.max_norm,
            rise_c=m.rise_c, altitude_m=h.altitude_m,
            heater_type=req.heaters.type if req.heaters else "gas_open",
            pads=Pads(req.pads.pad_type, req.pads.thickness_mm) if req.pads else None,
            fan_curve=tuple((pt.pressure_pa, pt.flow_m3h) for pt in req.fan_curve.points) if req.fan_curve else None,
            summer_target_c=req.summer_target_c,
            airtightness=h.airtightness.to_core() if h.airtightness else None))
    except (CalcError, MethodNotApplicable):
        raise
    except ValueError as ex:
        raise CalcError("invalid_parameters", message=str(ex))
    rid = _rid(request)
    body = {"request_id": rid, "app_version": _app_version(), "envelope": d.envelope, "days": d.days,
            "equipment": d.equipment, "sources": _sources(d.sources), "assumptions": d.assumptions,
            "warnings": d.warnings}
    _audit({"request_id": rid, "path": request.url.path, "status": 201,
            "request": req.model_dump(exclude_none=True),
            "result": {k: d.equipment[k] for k in ("min_ventilation_capacity_m3h", "max_ventilation_capacity_m3h",
                                                    "heating_capacity_kw")}})
    return JSONResponse(body, status_code=201)
