"""
Профили генетики — уставки по весу и кривые роста растущих свиней (этап 3).

Аналог «расчёта по кроссу» в MINMAXapp: руководство селекционера даёт
температуру и вентиляцию по весу, калькулятор считает по выбранному профилю.

Главная ловушка — СМЫСЛ уставки (docs/sources.md, 2.2):

  room_temperature  — температура помещения, которую надо держать (PIC «нужная
                      температура», Hypor, DanBred, РД-АПК «расчётная»);
  ventilation_start — температура, с которой вентиляция начинает расти выше
                      минимума (Klimaatplatform «begintemperatuur ventilatie»).
                      Отопление включается на нейтральную зону НИЖЕ — 1–2 °C.

Одинаковые числа в двух столбцах значат разное: если сложить их в одну колонку,
расчёт тепла зимой ошибётся на градусы. Поэтому профиль отдаёт и уставку как в
источнике, и расчётную температуру помещения для балансов (design_room_temp_c).

Фазы: доращивание и откорм идут в разных помещениях, и на стыке (≈ 23–30 кг)
уставки могут не совпадать — у Klimaatplatform конец доращивания 20 °C, начало
откорма 25 °C при одном весе. Кривые хранятся по фазам; профиль без такого
разрыва (PIC, Hypor) отдаёт одну кривую на обе фазы.

Числа — из первоисточников в refs/ (docs/sources.md, раздел 2), проверены по тексту.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from .errors import CalcError
from .pig_profile import PIC_TABLE_41

SetpointMeaning = Literal["room_temperature", "ventilation_start"]
Phase = Literal["nursery", "finisher"]

# Klimaatplatform 2021: «Hanteer voor de neutrale zoneverwarming 1 tot 2 graden» —
# отопление включается на 1–2 °C ниже начала вентиляции. Берём середину
NEUTRAL_ZONE_C = 1.5

# Граница фаз по умолчанию, если фаза не задана, — своя у профиля: выход с
# доращивания у источников разный (Klimaatplatform — 23 кг, DanBred — 30,
# РД-АПК — «отъёмыши» до 40 кг). У профилей с одной кривой на обе фазы она не важна
DEFAULT_NURSERY_MAX_KG = 25.0

Curve = tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class PhaseCurves:
    temp_by_weight: Curve                     # (кг, °C) — уставка как в источнике
    min_vent_by_weight: Optional[Curve] = None  # (кг, м³/ч на голову)
    max_vent_by_weight: Optional[Curve] = None
    rh_by_weight: Optional[Curve] = None      # (кг, %) — верхний предел или уставка RH


@dataclass(frozen=True)
class GeneticsProfile:
    id: str
    name: str
    source_id: str
    setpoint_meaning: SetpointMeaning
    nursery: PhaseCurves
    finisher: PhaseCurves
    growth: Optional[Curve]                   # (день после отъёма, кг)
    rh_max_pct: Optional[float]               # общий предел RH, если нет кривой
    assumptions: tuple[dict, ...] = field(default_factory=tuple)
    nursery_max_kg: float = DEFAULT_NURSERY_MAX_KG
    builtin: bool = True                      # из руководства (код) или свой (genetics_store)


def _lerp(x: float, curve: Curve) -> float:
    """Линейная интерполяция; за краями — крайнее значение (вызывающий предупреждает)."""
    if x <= curve[0][0]:
        return curve[0][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return curve[-1][1]


def _inverse(y: float, curve: Curve) -> float:
    """Обратная интерполяция по монотонно растущей кривой: x, при котором значение = y."""
    return _lerp(y, tuple((b, a) for a, b in curve))


def _note(code: str, **params) -> dict:
    return {"code": code, "params": params} if params else {"code": code}


# ─── PIC 2019 (табл. 4.1) ─────────────────────────────────────────────────────
# «Нужная температура» — температура помещения; строки без брудеров. Рост — та же
# таблица: день после отъёма ↔ средний вес

_PIC = PhaseCurves(
    temp_by_weight=tuple((r.avg_weight_kg, r.target_temp_c) for r in PIC_TABLE_41),
    min_vent_by_weight=tuple((r.avg_weight_kg, r.min_vent_m3_head_h) for r in PIC_TABLE_41),
    rh_by_weight=None,
)
PIC_2019 = GeneticsProfile(
    id="pic_2019", name="PIC (Wean to Finish 2019)", source_id="pic_2019_w2f",
    setpoint_meaning="room_temperature", nursery=_PIC, finisher=_PIC,
    growth=tuple((float(r.day_post_weaning), r.avg_weight_kg) for r in PIC_TABLE_41),
    rh_max_pct=65.0,
)


# ─── Hypor 2023 ───────────────────────────────────────────────────────────────
# W2F Manual, табл. 7 (с. 12), «measured at pig level»: полосы веса сплошные —
# каждая начинается с температуры, которой кончилась предыдущая (31→27, 27→26 …),
# поэтому это опорные точки одной кривой

_HYPOR = PhaseCurves(
    temp_by_weight=((5, 31), (6, 27), (8, 26), (10, 24), (12, 23), (17, 22),
                    (25, 21), (40, 19), (60, 18), (105, 16)),
    rh_by_weight=None,
)
# Рост: доращивание — Feeding Guide, табл. 2 (6.0 → 25.9 кг за 42 дня);
# откорм — Magnus, табл. 4 (понедельно с 25.0 кг). Таблицы расходятся на стыке
# на 0.9 кг; 25.9 кг в табл. 4 — через 1.1 сут от её начала, поэтому откорм
# сдвинут на 42 − 1.1 сут (наше сшивание, допущение профиля)
_MAGNUS_WEEKLY = (25.0, 30.7, 36.5, 42.5, 48.6, 54.9, 61.3, 68.0, 74.8, 81.9, 89.3,
                  96.8, 104.3, 111.6, 118.7, 125.5, 132.0, 138.2)
_HYPOR_JOIN_SHIFT_D = 42.0 - (25.9 - 25.0) / (30.7 - 25.0) * 7.0
HYPOR_2023 = GeneticsProfile(
    id="hypor_2023", name="Hypor (W2F Manual 2023, Magnus)", source_id="hypor_2023",
    setpoint_meaning="room_temperature", nursery=_HYPOR, finisher=_HYPOR,
    growth=((0.0, 6.0), (8.0, 8.0), (15.0, 10.3), (31.0, 18.2), (42.0, 25.9))
    + tuple((round(_HYPOR_JOIN_SHIFT_D + 7.0 * w, 2), kg) for w, kg in enumerate(_MAGNUS_WEEKLY) if kg > 25.9),
    rh_max_pct=75.0,      # «RH 55–75 %» — верхний край диапазона
    assumptions=(_note("growth_tables_joined", shift_days=round(_HYPOR_JOIN_SHIFT_D - 42.0, 2)),),
)


# ─── Klimaatplatform Varkenshouderij 2021 (Topigs Norsvin TN70, табл. 12) ────
# Таблица по ДНЯМ фазы. Вес дня: откорм — TN Select progeny, табл. 1 (кастраты)
# со сдвигом так, чтобы день 1 = 23 кг («circa 23 kg» в самой таблице);
# доращивание — посадка 7.5 кг, выход на 42-й день = 23 кг (тот же поросёнок),
# 21-й день — по форме кривой доращивания Hypor (36.5 % прироста периода, HYPOTHESIS)

_TN_SELECT_CASTRATES = ((1, 20.4), (8, 24.9), (15, 30.0), (22, 35.6), (29, 41.6), (36, 48.1),
                        (43, 54.9), (50, 62.0), (57, 69.3), (64, 76.8), (71, 84.4), (78, 92.1),
                        (85, 99.7), (92, 107.3), (99, 114.8), (106, 122.1), (113, 129.2))
_NL_FINISHER_DAY1_KG = 23.0
_NL_TN_SHIFT_D = _inverse(_NL_FINISHER_DAY1_KG, _TN_SELECT_CASTRATES) - 1.0


def _nl_finisher_kg(day: float) -> float:
    return _lerp(day + _NL_TN_SHIFT_D, _TN_SELECT_CASTRATES)


_HYPOR_NURSERY_SHARE_D21 = (_lerp(21.0, HYPOR_2023.growth) - 6.0) / (25.9 - 6.0)
_NL_NURSERY_KG = {0: 7.5, 21: 7.5 + _HYPOR_NURSERY_SHARE_D21 * (_NL_FINISHER_DAY1_KG - 7.5),
                  42: _NL_FINISHER_DAY1_KG}

# (день, мин. м³/ч, макс. м³/ч, начало вентиляции °C) — таблица дословно;
# доращивание на посадке: 26 °C на сплошном подогреваемом полу, 28 °C на полностью
# щелевом — берём щелевой (вариант без подогрева пола)
_NL_NURSERY_ROWS = ((0, 3, 12, 28), (21, 6, 18, 24), (42, 8, 25, 20))
_NL_FINISHER_ROWS = ((1, 6, 40, 25), (5, 8, 40, 22), (50, 14, 80, 20), (100, 17, 80, 19))


def _nl_phase(rows, kg_of_day) -> PhaseCurves:
    kg = [round(kg_of_day(d), 2) for d, *_ in rows]
    return PhaseCurves(
        temp_by_weight=tuple((k, r[3]) for k, r in zip(kg, rows)),
        min_vent_by_weight=tuple((k, r[1]) for k, r in zip(kg, rows)),
        max_vent_by_weight=tuple((k, r[2]) for k, r in zip(kg, rows)),
    )


KLIMAATPLATFORM_2021 = GeneticsProfile(
    id="klimaatplatform_2021", name="Klimaatplatform NL 2021 / Topigs Norsvin", source_id="klimaatplatform_2021",
    setpoint_meaning="ventilation_start",
    nursery=_nl_phase(_NL_NURSERY_ROWS, lambda d: _NL_NURSERY_KG[d]),
    finisher=_nl_phase(_NL_FINISHER_ROWS, _nl_finisher_kg),
    growth=tuple((float(d), round(k, 2)) for d, k in _NL_NURSERY_KG.items())
    + tuple((42.0 + d, k) for d, k in ((d, round(_nl_finisher_kg(d), 2)) for d in (1, 5, 50, 100))),
    rh_max_pct=None,
    assumptions=(_note("nursery_day21_weight_from_hypor_shape", kg=round(_NL_NURSERY_KG[21], 2)),
                 _note("nursery_fully_slatted_floor")),
    nursery_max_kg=_NL_FINISHER_DAY1_KG,
)


# ─── DanBred (web manual, двухклиматный станок) ───────────────────────────────
# «Set temp. room» и «Set humidity room» по весу 5.5–30 кг — только доращивание

_DANBRED_NURSERY = PhaseCurves(
    temp_by_weight=((5.5, 25.5), (6, 24), (7, 24), (8.5, 23), (11, 22), (15, 21), (18, 20), (25, 19), (30, 18)),
    rh_by_weight=((5.5, 60), (6, 61), (7, 61), (8.5, 62), (11, 63), (15, 64), (18, 70), (25, 71), (30, 72)),
)
DANBRED = GeneticsProfile(
    id="danbred", name="DanBred (двухклиматный станок, до 30 кг)", source_id="danbred_manual",
    setpoint_meaning="room_temperature", nursery=_DANBRED_NURSERY, finisher=_DANBRED_NURSERY,
    growth=None, rh_max_pct=None,
    assumptions=(_note("range_midpoint", where="5.5 кг: 25–26 °C"),),
    nursery_max_kg=30.0,
)


# ─── РД-АПК 1.10.02.04-12, табл. 17 ───────────────────────────────────────────
# Расчётная температура холодного и переходного периодов: поросята-отъёмыши 24 °C
# (22–28), откорм 18 °C (14–20); RH не более 70 %. Граница групп по весу — 40 кг:
# по ней в табл. 15 кончаются «отъёмыши» и начинается «откормочный молодняк»

_RD_NURSERY = PhaseCurves(temp_by_weight=((5.0, 24.0), (40.0, 24.0)))
_RD_FINISHER = PhaseCurves(temp_by_weight=((40.0, 18.0), (160.0, 18.0)))
RD_APK_2012 = GeneticsProfile(
    id="rd_apk_2012", name="РД-АПК 1.10.02.04-12 (норма РФ)", source_id="rd_apk_2012",
    setpoint_meaning="room_temperature", nursery=_RD_NURSERY, finisher=_RD_FINISHER,
    growth=None, rh_max_pct=70.0, nursery_max_kg=40.0,
)


PROFILES: dict[str, GeneticsProfile] = {p.id: p for p in
                                        (PIC_2019, HYPOR_2023, KLIMAATPLATFORM_2021, DANBRED, RD_APK_2012)}


def find(profile_id: str) -> Optional[GeneticsProfile]:
    """Профиль по id: встроенный, затем свой. Встроенный id своим не перекрыть (save)."""
    from . import genetics_store
    return PROFILES.get(profile_id) or genetics_store.load(profile_id)


def all_profiles() -> list[GeneticsProfile]:
    """Встроенные, затем свои — порядок списка в интерфейсе и API."""
    from . import genetics_store
    return list(PROFILES.values()) + genetics_store.list_custom()


# ─── Запрос уставок ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Setpoints:
    profile_id: str
    phase: str
    weight_kg: float
    setpoint_c: float                     # как в источнике
    setpoint_meaning: str
    design_room_temp_c: float             # для балансов тепла и влаги
    min_vent_m3h: Optional[float]
    max_vent_m3h: Optional[float]
    rh_pct: Optional[float]
    source_id: str
    assumptions: list[dict]
    warnings: list[dict]


def weight_at_day(profile: GeneticsProfile, day: float) -> float:
    if profile.growth is None:
        raise ValueError(f"profile {profile.id} has no growth curve — pass weight_kg")
    return _lerp(day, profile.growth)


def setpoints(profile_id: str, weight_kg: Optional[float] = None, day: Optional[float] = None,
              phase: Optional[Phase] = None) -> Setpoints:
    profile = find(profile_id)
    if profile is None:
        # Свой профиль могли удалить между проверкой запроса и расчётом
        raise CalcError("genetics_not_found", id=profile_id)
    assumptions = list(profile.assumptions)
    warnings: list[dict] = []

    if weight_kg is None:
        if day is None:
            raise ValueError("weight_kg or day is required")
        weight_kg = round(weight_at_day(profile, day), 2)
        assumptions.append(_note("weight_from_growth_curve", day=day, weight_kg=weight_kg))
        if profile.growth and not profile.growth[0][0] <= day <= profile.growth[-1][0]:
            warnings.append(_note("day_outside_growth_curve", min_day=profile.growth[0][0],
                                  max_day=profile.growth[-1][0]))

    if phase is None:
        phase = "nursery" if weight_kg <= profile.nursery_max_kg else "finisher"
        if profile.nursery is not profile.finisher:
            assumptions.append(_note("phase_from_weight", phase=phase, nursery_max_kg=profile.nursery_max_kg))
    curves = profile.nursery if phase == "nursery" else profile.finisher

    lo, hi = curves.temp_by_weight[0][0], curves.temp_by_weight[-1][0]
    if not lo <= weight_kg <= hi:
        warnings.append(_note("weight_outside_profile", phase=phase, min_kg=lo, max_kg=hi))

    setpoint = round(_lerp(weight_kg, curves.temp_by_weight), 1)
    if profile.setpoint_meaning == "ventilation_start":
        design = round(setpoint - NEUTRAL_ZONE_C, 1)
        assumptions.append(_note("heating_below_ventilation_start", neutral_zone_c=NEUTRAL_ZONE_C))
    else:
        design = setpoint

    def at(curve: Optional[Curve]) -> Optional[float]:
        return round(_lerp(weight_kg, curve), 1) if curve else None

    rh = at(curves.rh_by_weight)
    return Setpoints(
        profile_id=profile.id, phase=phase, weight_kg=weight_kg,
        setpoint_c=setpoint, setpoint_meaning=profile.setpoint_meaning,
        design_room_temp_c=design,
        min_vent_m3h=at(curves.min_vent_by_weight), max_vent_m3h=at(curves.max_vent_by_weight),
        rh_pct=rh if rh is not None else profile.rh_max_pct,
        source_id=profile.source_id, assumptions=assumptions, warnings=warnings,
    )
