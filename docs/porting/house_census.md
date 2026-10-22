# Перепись переноса: физика корпуса MINMAXapp → PigMaxApp (этап 4)

Метод — скилл faithful-porting: сначала реестр, потом перенос 1:1 по нему, потом
поиск пропусков. Исходник — MINMAXapp `backend/app/core/`, коммит 59390c7.
У каждого перенесённого модуля в шапке есть пометка `Source:` с путём исходника.

Решения:

| Решение | Значение |
|---|---|
| **PORT** | 1:1, меняются только импорты |
| **ADAPT** | та же логика, заменён механизм: давление передаётся явно вместо `site_pressure_pa()` из настроек хозяйства, тепло свиней вместо птицы |
| **SKIP** | не переносится — причина указана, это записанный пробел |

## Мосты (меняются во всех модулях одинаково)

- `psychro.site_pressure_pa()` (высота фермы из настроек MINMAXapp) → параметр
  `p_pa=P_ATM_PA`. У PigMaxApp нет настроек хозяйства, высота приходит в запросе.
- `bird_heat.house_heat_per_bird(w, t).sensible_w` → `pig_heat(group, w, t).sensible_w`
  (CIGR 2002 для свиней, этап 1).
- Имена «bird/birds» → «animal/animals» в полях и статусах
  (`q_birds_kw` → `q_animals_kw`, `birds_sufficient` → `animals_sufficient`).

## errors.py

| Единица | Решение |
|---|---|
| `CalcError.__init__`, `_message`, `detail` | PORT |

## psychro.py (дополнение к этапу 2)

| Единица | Решение |
|---|---|
| `sat_pressure_ice_pa`, `_w_from_wet_bulb`, `wet_bulb_c` | ADAPT (p явно) |
| `dew_point_c` | PORT |
| `specific_volume_m3_per_kg_da` | ADAPT (p явно) |
| `moist_air_density` | ADAPT (p явно) |
| `site_pressure`, `site_pressure_pa`, `_pressure_override`, `_p` | SKIP — механизм заменён явным `p_pa` |

## building_thermal.py

| Единица | Решение |
|---|---|
| `BuildingDimensions` | PORT |
| `U_ENVELOPE_MIN_W_M2K`, `U_ENVELOPE_MAX_W_M2K`, `envelope_area_m2`, `R_SI_M2K_W` | PORT |
| `surface_condensation` | PORT |
| `AIR_CP_J_PER_KG_K` | PORT |
| `air_density_kg_m3` | ADAPT (p явно) |
| `heater_air_demand` | PORT |
| `heater_co2_dilution_m3h` | PORT + параметры `limit_ppm=3000`, `outdoor_ppm=400` (по умолчанию значения исходника: ΔCO₂ = 2.6 л/м³) |
| `air_temperature_rise_c` | ADAPT (тепло свиней) |
| `ventilation_heat_w` | ADAPT (p явно) |
| `HeaterDuty`, `solve_heater_duty` | PORT (`birds_sensible_w` → `animals_sensible_w`) |
| `HeatBalance`, `calculate_heat_balance` | ADAPT (тепло свиней, имена полей) + необязательные `animals_sensible_w` (явное тепло поголовья, посчитанное снаружи — свиноматки, другая методика, ks) и `co2_limit_ppm` / `co2_outdoor_ppm` для горелок; по умолчанию поведение исходника |
| `bird_sensible_heat_w` | ADAPT → `animal_sensible_heat_w` |
| `HeaterConfig`, `CalibrationTest`, `ThermalConfig`, `THERMAL_CONFIG_PATH`, хранилище по корпусам (`_config_*`, `load_*`, `save_*`, `rename_house`, `HouseIdTaken`), `usable_ua`, `calibration_problem`, `calculate_ua_coefficient`, `MIN_CALIBRATION_MINUTES` | **SKIP** — калибровка UA по тесту остывания и её хранение. PigMaxApp считает UA по конструкции (`construction.py`) или берёт введённый. Пробел: калибровка по факту — в этапе интерфейса |

## construction.py — PORT целиком

`Material`, `_MATERIALS` (15 материалов, λ дословно), `MATERIALS`, `R_SE_M2K_W`,
`R_SI_HORIZONTAL_M2K_W`, `R_SI_UPWARD_M2K_W`, `_STEEP_ROOF_DEG`,
`surface_resistance_si`, `_LAMBDA_MIN`, `_LAMBDA_MAX`, `Layer`, `Surface`,
`layer_resistance`, `surface_u`, `HouseGeometry`, `EnvelopeAreas`,
`envelope_areas`, `EnvelopeUA`, `envelope_ua`.

## design_climate.py — PORT целиком

`ARCHIVE_URL`, `YEARS`, `SOURCE`, `DesignConditions`, `_percentile`,
`design_conditions`, `fetch_hourly`. Метод API (`/climate-designs`) — только на
этапе API v1 с ключами и лимитами: запрос к Open-Meteo тратит бесплатную квоту.

## evap_cooling_calc.py — PORT целиком

`_V_DESIGN_BY_THICKNESS`, `design_pad_velocity_ms`, `SANHE_PADS` (3 типа, таблицы
дословно), `PAD_TYPE_DEFAULT`, `EvapCoolingInput` (ADAPT: `num_birds`,
`bird_weight_kg` → `num_animals`, `animal_weight_kg`; + `p_pa`),
`EvapCoolingResult`, `_interp_linear`, `_water_flow_lpm` (ADAPT p),
`FAN_RATING_POINT_PA`, `_pad_pressure_pa`, `pad_operating_airflow_m3s`,
`calculate_evap_cooling` (ADAPT p, тепло свиней).

## tunnel_speed_calc.py — частично

| Единица | Решение |
|---|---|
| `FAN_RATING_POINT_PA`, `RHO_STD_AIR`, `CD_INLET`, `CD_INLET_OPEN` | PORT |
| `_fan_operating_flow_m3s`, `_operating_flow_with_pad_m3s` | PORT |
| `_EFF_TEMP_TABLE`, `_T_KEYS`, `_RH_KEYS`, `_V_KEYS`, `_interp_v`, `_table_value`, `_wind_cooling_c`, `_effective_temperature`, `_T_WIND_*`, `_T_BIRD_SURFACE_C` | **SKIP** — эффективная температура ПТИЦЫ. Свиной аналог (McFarlane, Close) — новый модуль `pig_effective_temp.py`, не перенос |
| `_THVI_THRESHOLDS`, `_THVI_V_RANGE`, `_calc_thvi` | **SKIP** — индекс THVI бройлеров |
| `_SPEED_TARGETS`, `target_speed_range`, `EFFECTIVE_TEMP_MAX_C`, `_T_LIMIT_CAP`, `_find_tunnel_temp_limit`, `MAX_END_TO_END_RISE_C`, `TunnelSpeedInput`, `TunnelSpeedResult`, `_lerp`, `calculate_tunnel_speed` | **SKIP** — инструмент туннеля по скоростям для птицы по возрасту (Cobb). Для свиней скорость — по весу (RD-APK табл. 18, PIC); пересобирается на этапе проектного расчёта |

## tunnel_design.py — PORT целиком

`HIGH_TUNNEL_PRESSURE_PA`, `Pads`, `TunnelPressure`, `FanSelection`,
`pad_area_m2`, `_pad_table`, `Resistance` (`__init__`, `parts`, `__call__`),
`check_fan_curve`, `fan_flow_m3h`, `_operating_point`, `select_fans`.

## Не переносятся

| Модуль | Причина |
|---|---|
| `apparent_temp.py` | поправка уставки по RH для бройлеров (Ross табл. 2.5); у свиней аналога нет |
| `leakage_calc.py` | тест герметичности — инструмент обслуживания, а не проектного расчёта (пробел) |
| `cooling_fit.py` | калибровка UA по кривой остывания — вместе с хранилищем калибровки (пробел) |

## Источники (`sources.py`) — PORT записей

`br443_2019_s3`, `br443_2019_s4_3`, `marcegaglia_dop_100p_2022`,
`ashrae_2017_ch1`, `era5_open_meteo`, `sanhe_pads`, `uga_pad_velocity`, `amca_210`.

## Проход 3 — итог (2026-10-22)

- **Трассируемость** (`traceability_check.py --window 16`): 52/52 функции с пометкой
  `Source:`, все ссылки разрешаются в MINMAXapp. Окно 16 строк — из-за многострочных
  сигнатур: пометка стоит первой строкой тела.
- **Перепись констант** (`gap_check.py`): 18/46 перенесено. Все 28 отсутствующих —
  SKIP этого реестра: калибровка (`building_thermal`), `leakage_calc`, `cooling_fit`,
  птичья часть `tunnel_speed_calc`, `apparent_temp`. Неожиданных пропусков нет.
- **Перепись функций** (сравнение имён по модулям): отсутствуют ровно единицы SKIP,
  лишних функций в порте нет.
- **Паритет поведения**: те же входы — в оригинале и в порте. 218 + 5 сценариев
  решателя горелок, расхождений нет (точность 1e-9), ветки решателя — полная,
  частичная (бисекция), нулевая загрузка, электрические, пол обмена. Значения
  оригинала заморожены в `backend/tests/data/minmax_house_parity_59390c7.json`,
  тест `test_house_parity.py` держит паритет при будущих правках. Проверка от
  обратного: R_se 0.04 → 0.05, одна точка таблицы SANHE и запас горелок 1.5 → 1.4 —
  тест краснеет на каждом.

## Дополнение этапа 8.1 — утечки, конденсат, тест герметичности (2026-10-22)

| Единица MINMAXapp | Решение |
|---|---|
| `leakage_calc.py` целиком | PORT (было SKIP) — тест герметичности `/calc/leakage` |
| `ventilation_design.py`: `Airtightness`, `AIRTIGHTNESS_CLASS_RELATIVE`, `DEFAULT_AIRTIGHTNESS_CLASS`, `NATURAL_LEAK_PRESSURE_PA`, `leak_at_4pa_m3h`, `coldest_surface`, `condensation`, `airtightness_q4` | PORT → `core/house_checks.py`; мост: заметка — словарь вместо `Note` |
| `building_thermal.calculate_heat_balance` | ADAPT: `+ exchange_floor_m3h` (утечка при 4 Па) — передаётся решателю; потери с воздухом от max(минимум, утечка). По умолчанию 0 — поведение исходника |

- **Паритет**: `probe_leakage.py`. 20 групп совпали с оригиналом, включая обе ветки
  конденсата, ошибку близких точек и решатель с полом обмена. Эталон:
  `minmax_leakage_parity_59390c7.json`.
- **Подключено**:
  - «сейчас» — при заданных размерах корпуса;
  - «проект» — всегда, без данных класс `moderate`;
  - коды `leakage_exceeds_min_vent`, `condensation_risk`, `airtightness_class_assumed`;
  - поля `air_exchange_basis` и `heating_air_basis`.

## Дополнение этапа 8.3 — калибровка UA по кривой остывания (2026-10-22)

| Единица MINMAXapp | Решение |
|---|---|
| `cooling_fit.py` целиком | PORT (было SKIP); мост: `p_pa` явно |
| `building_thermal`: `CalibrationTest`, `MIN_CALIBRATION_MINUTES`, `calibration_problem` | PORT (было SKIP); метод по одной точке не переносился — у записи всегда `cooling_curve` |
| `routers/thermal.py`: `run_curve_calibration`, проверки при сохранении | ADAPT → `/calc/calibrate-ua` и `PUT /settings` с `calibration` (настройки корпуса PigMaxApp, без хранилища по корпусам) |

- **Паритет**: `probe_cooling.py`, расхождений нет, эталон
  `minmax_cooling_parity_59390c7.json`. На синтетической кривой UA восстановлен с
  точностью 1.2 %; пройдены ветки предупреждений, ошибок отметок и правдоподобия.
- **Пробел тестов**: ветка `calibration_ua_unresolved` маршрутом не проверена — не
  подобрана синтетика с неразличимым UA. Сам механизм (`ua_not_resolved`) перенесён
  1:1 и входит в паритет.

## Дополнение — редактор профилей генетики (2026-10-22)

- `json_store.py` — PORT 1:1 (`atomic_write_json`), разница с оригиналом — только метки `Source:`.
- `growth_profiles.py` — НЕ перенос, ADAPT по образцу: правила те же (файл на профиль,
  id из имени файла, встроенные только для чтения → 403, своя версия — копия под новым
  id, битый файл пропускается с записью в лог), формат свой — встроенные профили
  PigMaxApp живут в коде с пометками источника, на диске только свои
  (`core/genetics_store.py`, `PIG_GENETICS_DIR`).
- Запрет удалять профиль «по умолчанию» (409 `profile_is_default`) — перенесён с
  настройками хозяйства: по умолчанию можно выбрать и свой профиль. Порядок проверок
  как у MINMAXapp: «встроенный» (403) первичен.

## Дополнение — настройки хозяйства и резервная копия (2026-10-22)

- `routers/settings.py` → ADAPT: `AppConfig` MINMAXapp стал `FarmConfig` внутри
  `PigSettings` (один файл настроек). Перенесены: проверка существования профиля по
  умолчанию, `POST /settings/design-climate` под токеном, правило «высоту не трогать,
  если задана». Птичьи поля (`cycle_days`, `v_tunnel_ms`, `tunnel_min_*`,
  `safety_factor_qmin`) не переносятся — у PigMaxApp нет таких расчётов.
- `routers/backup.py` → ADAPT: тот же порядок «проверить всё → записать, профили до
  настроек»; части — `settings` и `genetics_profiles`. Список корпусов браузера у
  PigMaxApp отсутствует (корпус один).
- Настройки хозяйства только заполняют формы интерфейса; API v1 от них не зависит.

