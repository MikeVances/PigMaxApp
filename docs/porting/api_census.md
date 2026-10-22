# Перепись переноса: API v1 MINMAXapp → PigMaxApp (этап 6)

Исходник — MINMAXapp 59390c7: `backend/app/api_v1.py`, `api_access.py`,
`core/paths.py`; контракт `docs/api/v1/openapi.yaml`, `errors.md`. Метод — скилл
faithful-porting, решения те же: PORT, ADAPT, SKIP. Новый код PigMaxApp помечен
NEW, пометки `Source:` у него нет.

## api_access.py — PORT целиком

`API_KEY_HEADER`, `WINDOW_S`, `LIMITS` (calc 60, catalog 300, climate 10 в
минуту), `PUBLIC_PATHS`, `CALC_PATHS` (`/ventilation-designs`,
`/ventilation-requirements` — сценарии PigMaxApp названы так же), `CLIMATE_PATHS`,
`configured_keys`, `key_id`, `match_key`, `route_group`, `Verdict`,
`RateLimiter` (`hit`, `reset`), `limiter`.

## core/paths.py — PORT целиком

`PROJECT_ROOT`, `DATA_DIR`, `data_path`.

## api_v1.py

| Единица | Решение |
|---|---|
| `API_VERSION`, `ERRORS_DOC` | ADAPT — ссылка на errors.md PigMaxApp |
| `api` (подприложение, `/docs`, `/openapi.json`) | ADAPT — заголовок |
| `AUDIT_LOG_PATH`, `_audit_logger`, `_audit_lock`, `_audit` | PORT; путь по умолчанию `backend/data/api_audit.jsonl` (данные PigMaxApp лежат в `backend/data/`) |
| `_client_var`, `_route_path`, `_check_access`, `_request_id` (middleware), `_rid` | PORT |
| `_STATUS_BY_CODE`, `_problem` | PORT; `+ genetics_not_found`, `sow_profile_not_found` → 404 |
| обработчики `CalcError`, `RequestValidationError` | PORT |
| обработчик `ProfileNotFound` | SKIP — профилей роста MINMAXapp нет; профили генетики — словарь, 404 через `CalcError` |
| NEW: обработчик `MethodNotApplicable` | 422 `method_not_applicable` |
| `_Strict` | PORT |
| `LayerIn`, `SurfaceIn`, `EnvelopeIn` | ADAPT — без `AirtightnessIn` (тест герметичности не перенесён, этап 4) |
| `CageRowIn`, `HouseIn`, `FlockIn`, `FlockNowIn`, `ConditionsIn`, `TunnelRulesIn`, `DesignRequest`, `RequirementRequest`, `ClimateIn`, `HeatersIn`, `PadsIn`, `FanPointIn`, `FanCurveIn` | SKIP / NEW — модели сценариев птичника (клетки, кросс, дни выращивания); у свиней свои модели — этап 6б |
| `create_ventilation_design`, `create_ventilation_requirement`, `_heater`, `_house_parts`, `_envelope_parts`, `_note`, `_sources` | NEW на этапе 6б (сценарии свиней) |
| `_CATALOG_CACHE`, `list_construction_materials` | PORT |
| `SOURCE_ID_PATTERN`, `list_sources`, `get_source` | PORT |
| `list_growth_profiles`, `get_growth_profile`, `_source_entry` | SKIP → NEW `list_genetics`, `get_genetics`, `list_sow_profiles` |
| `_climate_cache`, `_climate_lock`, `get_climate_design` | PORT |
| `CONTRACT_PATH`, `contract` | PORT |
| `list_housing_systems` | SKIP — у свиней нет выбора «пол / клетки» |
| `_app_version` | PORT |

## Проход 3 — итог этапа 6а (2026-10-22)

- **Трассируемость**: 25/29. Не отслежены ровно функции NEW: `list_genetics`,
  `get_genetics` с вложенной `phase`, `list_sow_profiles`.
- **Ложная засечка инструмента**: обработчик `_method_not_applicable` — тоже NEW,
  но засчитан как отслеженный. Окно в 16 строк дотягивается до пометки соседней
  функции `_validation`.
- **`api_access.py` и `core/paths.py`** отличаются от исходника только шапкой и
  пометками `Source:` (diff).
- **Тесты доступа** перенесены из MINMAXapp `test_api_v1_access.py`. Расчётная
  группа и сверка с контрактом — в этапе 6б.
- **Мутации**: отключённая проверка ключа, кэш климата без округления, ключ в
  журнал вместо отпечатка, 404 профиля как 422 — тесты поймали все четыре.
