# Коды API v1 PigMaxApp

Ответы содержат машинные коды. Текст на языке пользователя собирает клиент, а
агент пересказывает его своими словами. Коды стабильны: переименование ломает
клиентов, поэтому только добавляем новые.

Тест `test_api_v1_contract.py` проверяет, что каждый код, который реально
приходит в ответах, описан здесь.

## Ошибки (RFC 7807, `application/problem+json`)

| Код | HTTP | Когда |
|---|---|---|
| <a id="bad_request"></a>`bad_request` | 400 | тело запроса — не JSON |
| <a id="validation_failed"></a>`validation_failed` | 422 | поля не прошли проверку; подробности — в `errors` по полям |
| <a id="invalid_parameters"></a>`invalid_parameters` | 422 | поля по отдельности верны, но вместе считать по ним нельзя (у профиля нет кривой роста, нет нормы на голову и т. п.); причина — `params.message` |
| <a id="method_not_applicable"></a>`method_not_applicable` | 422 | методика тепла не покрывает группу: `brown_brandl_2004` — только растущие, `stinn_xin_2014` — только свиноматки |
| <a id="envelope_implausible"></a>`envelope_implausible` | 422 | средний U ограждения вне 0.1–8 Вт/(м²·К) — опечатка в толщине или U |
| <a id="fan_curve_invalid"></a>`fan_curve_invalid` | 422 | точки вентилятора не «давление растёт — расход падает», или каталог не того класса |
| <a id="api_key_required"></a>`api_key_required` | 401 | на площадке заданы ключи, а `X-API-Key` нет или он неверный |
| <a id="rate_limited"></a>`rate_limited` | 429 | превышен лимит ключа в минуту; `Retry-After` — через сколько повторить |
| <a id="source_not_found"></a>`source_not_found` | 404 | нет источника с таким `source_id` |
| <a id="material_not_found"></a>`material_not_found` | 404 | нет материала в справочнике |
| <a id="genetics_not_found"></a>`genetics_not_found` | 404 | нет профиля генетики |
| <a id="sow_profile_not_found"></a>`sow_profile_not_found` | 404 | нет профиля климата свиноматок |
| <a id="contract_not_found"></a>`contract_not_found` | 404 | на площадке не выложен `docs/api/v1/openapi.yaml` |
| <a id="climate_unavailable"></a>`climate_unavailable` | 502 | Open-Meteo не ответил или прислал меньше года данных |

## Предупреждения (`warnings`)

| Код | Смысл |
|---|---|
| <a id="humidity_uncontrollable"></a>`humidity_uncontrollable` | наружный воздух не суше целевого — вентиляцией RH не удержать, нужен подогрев или осушение |
| <a id="weight_outside_table"></a>`weight_outside_table` | вес вне табл. 4.1 PIC — уставки взяты с её края |
| <a id="weight_outside_profile"></a>`weight_outside_profile` | вес вне кривой профиля генетики — значения с её края |
| <a id="weight_outside_source_range"></a>`weight_outside_source_range` | вес вне данных методики тепла (Brown-Brandl 10–100 кг, строки РД-АПК) |
| <a id="temperature_outside_source_range"></a>`temperature_outside_source_range` | температура вне данных методики (РД-АПК −5…30, Stinn & Xin) |
| <a id="feed_level_extrapolated"></a>`feed_level_extrapolated` | тяжелее 120 кг — кратность кормления CIGR держится на значении 120 кг |
| <a id="day_outside_growth_curve"></a>`day_outside_growth_curve` | день вне кривой роста профиля |
| <a id="inside_above_rd_apk_limit"></a>`inside_above_rd_apk_limit` | летом внутри выше 28 °C (РД-АПК прим. 3) — вентиляцией не охладить, нужны панели или скорость |
| <a id="air_speed_beyond_data"></a>`air_speed_beyond_data` | скорость выше 1.52 м/с — данных McFarlane дальше нет, охлаждение держится на 10 °C |
| <a id="air_speed_cannot_reach_setpoint"></a>`air_speed_cannot_reach_setpoint` | скоростью воздуха не снять нужное охлаждение до уставки (больше 10 °C) |
| <a id="air_speed_cannot_reach_target"></a>`air_speed_cannot_reach_target` | то же для летней цели проекта |
| <a id="air_speed_above_rd_apk"></a>`air_speed_above_rd_apk` | нужная летом скорость выше допустимой по РД-АПК табл. 18 (откорм 1.0, доращивание 0.6 м/с) |
| <a id="tunnel_length_loss_not_included"></a>`tunnel_length_loss_not_included` | давление туннеля без потерь по длине корпуса — источника коэффициента нет, замер их заменяет |
| <a id="tunnel_pressure_high"></a>`tunnel_pressure_high` | рабочее давление выше 50 Па — осевые вентиляторы теряют расход, перепроверьте каталог и панели |
| <a id="fan_curve_extrapolated"></a>`fan_curve_extrapolated` | рабочая точка за последней точкой каталога вентилятора |

## Допущения (`assumptions`)

| Код | Смысл |
|---|---|
| <a id="feed_level"></a>`feed_level` | кратность кормления CIGR (столбец табл. 2.1 и n) |
| <a id="piglet_temperature_coefficient"></a>`piglet_temperature_coefficient` | поросята легче 20 кг: −2 %/°C полного тепла (рекомендация CIGR) |
| <a id="milk_yield"></a>`milk_yield` | молочность подсосной в ур. 10 CIGR (по умолчанию 6 кг/сут — устарело) |
| <a id="daily_gain"></a>`daily_gain` | привес свиноматки или хряка в ур. 9 CIGR |
| <a id="sensible_share_from_cigr"></a>`sensible_share_from_cigr` | у Brown-Brandl разделения нет — доля явного тепла по CIGR ур. 32 |
| <a id="vapour_animal_level"></a>`vapour_animal_level` | РД-АПК: пар от животного, без испарения с пола и навоза |
| <a id="floor_feed"></a>`floor_feed` | поправка ks на корм и пол (CIGR табл. 3.1) |
| <a id="gestation_stage"></a>`gestation_stage` | стадия супоросности Stinn & Xin (B/EG до 40-го дня, LG) |
| <a id="sow_and_litter_mass_from_source"></a>`sow_and_litter_mass_from_source` | масса свиноматки с гнездом не задана — взята масса из табл. 4 Stinn & Xin |
| <a id="co2_daily_average"></a>`co2_daily_average` | CO₂ — среднесуточно (CIGR); днём, в часы активности, выше |
| <a id="period_from_outside_temperature"></a>`period_from_outside_temperature` | период нормы РД-АПК по наружной температуре; граница холодного и переходного 0 °C — наша |
| <a id="boar_norm_from_breeding_area"></a>`boar_norm_from_breeding_area` | у PIC нормы для хряков нет — норма сектора осеменения |
| <a id="litter_mass_not_counted"></a>`litter_mass_not_counted` | норма РД-АПК на 100 кг — без массы гнезда, занижена |
| <a id="outside_rh_default"></a>`outside_rh_default` | наружная RH не задана — 80 % |
| <a id="rh_max_default"></a>`rh_max_default` | предел RH не задан и в профиле его нет — 65 % (PIC) |
| <a id="design_temperature_rise"></a>`design_temperature_rise` | летний подъём над наружной 3 °C (РД-АПК допускает до +5) |
| <a id="effective_temperature_empirical"></a>`effective_temperature_empirical` | эффективная температура свиней — эмпирика (McFarlane, Close), а не формула |
| <a id="wet_floor_midpoint"></a>`wet_floor_midpoint` | мокрый пол −8.5 °C — середина 7–10 (Mount 1975) |
| <a id="weight_from_growth_curve"></a>`weight_from_growth_curve` | вес взят по кривой роста профиля на заданный день |
| <a id="phase_from_weight"></a>`phase_from_weight` | фаза (доращивание или откорм) выбрана по весу и границе профиля |
| <a id="heating_below_ventilation_start"></a>`heating_below_ventilation_start` | уставка профиля — начало вентиляции; отопление на 1.5 °C ниже |
| <a id="growth_tables_joined"></a>`growth_tables_joined` | кривая роста Hypor сшита из двух таблиц по весу |
| <a id="nursery_day21_weight_from_hypor_shape"></a>`nursery_day21_weight_from_hypor_shape` | вес 21-го дня доращивания NL — по форме кривой Hypor (гипотеза) |
| <a id="nursery_fully_slatted_floor"></a>`nursery_fully_slatted_floor` | посадка доращивания NL — вариант полностью щелевого пола (28 °C) |
| <a id="range_midpoint"></a>`range_midpoint` | диапазон источника взят серединой |
| <a id="weaning_day_default"></a>`weaning_day_default` | срок отъёма для кривой гнезда РД-АПК — 28 дней (гипотеза) |
| <a id="sow_profile_default"></a>`sow_profile_default` | профиль климата свиноматок не задан — PIC 2021 |
| <a id="genetics_default"></a>`genetics_default` | профиль генетики не задан — PIC 2019 |
| <a id="inside_temperature_given"></a>`inside_temperature_given` | температура помещения задана запросом, а не уставкой профиля |
| <a id="envelope_not_given"></a>`envelope_not_given` | ограждение не задано — потери через него не учтены |
| <a id="creep_heat_not_counted"></a>`creep_heat_not_counted` | мощность обогрева гнёзд не задана — её тепло в балансе помещения не учтено |
| <a id="pre_farrowing_as_late_gestation"></a>`pre_farrowing_as_late_gestation` | до опороса у методик без этой стадии — супоросная на 112-й день |
| <a id="profile_has_no_ventilation_norm"></a>`profile_has_no_ventilation_norm` | у профиля нет нормы вентиляции на голову |
| <a id="gas_heater_treated_as_open_flame"></a>`gas_heater_treated_as_open_flame` | газовый отопитель считается открытым пламенем (CO₂ и пар в помещение) |
| <a id="max_equals_min_in_heating"></a>`max_equals_min_in_heating` | наружная с подъёмом холоднее помещения — режим отопления: максимум равен минимуму, сверх него воздуха не нужно |
| <a id="mortality_linear"></a>`mortality_linear` | падёж цикла распределён по дням линейно |
| <a id="heating_without_heater_dilution_air"></a>`heating_without_heater_dilution_air` | мощность отопления проекта — без воздуха разбавления CO₂ самих горелок |
| <a id="season_ignored_for_profile"></a>`season_ignored_for_profile` | интерфейс: у профиля генетики (кроме PIC) нет зимней и летней уставки — сезон не учитывается |
| <a id="summer_target_rd_apk"></a>`summer_target_rd_apk` | летняя цель 26 °C — нижний край «26–28 °C» РД-АПК прим. 3 |
