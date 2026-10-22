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
from typing import Any, Optional

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
from .core.pig_heat import MethodNotApplicable

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
