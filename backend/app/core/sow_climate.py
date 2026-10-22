"""
Климат свиноматок по источникам — помещение и гнездо поросят (этап 5).

Подсосный станок — два климата в одном месте: свиноматке нужно ~20 °C, поросятам
в гнезде 30–35 °C. Помещение держат по свиноматке, гнездо греют местно (лампы,
подогрев пола) — это тепло тоже уходит в помещение и входит в его баланс.

Уставка помещения в опороснике меняется по дням лактации — задаётся точками
(день лактации, °C); −3 — за несколько дней до опороса. Числа сверены с refs/:

  PIC Gilt & Sow 2021, табл. 3.1: осеменение и супоросность 18–20 °C, RH < 65 %;
      табл. 5.2: 21–23 °C при опоросе, «gradually dropping … to 19 °C by day 7–10»;
      гнездо 32–35 °C (90–95 °F, раздел подготовки опоросника).
  Hypor Libra Farrowing 2.0: вокруг опороса 23–24, 1-я неделя 22–23, до отъёма 20–21 °C;
      гнездо: день опороса 34–35, 1-я неделя 32–33, до отъёма 30–31 °C.
  Klimaatplatform 2021: начало вентиляции — холостые 20, супоросные 20, до опороса 20,
      при опоросе 23, через неделю 20 (18 при крытом гнезде), конец 20 (18); мин./макс.
      вентиляция на свиноматку 18/150, 25/150, 25/250, 25/250, 35/250, 60/250 м³/ч;
      пол гнезда: день 1 — 33–35, день 7 — 29–31, день 25 — 23–26 °C.
  РД-АПК 1.10.02.04-12, табл. 17: холостые и супоросные 16 °C (13–19), RH ≤ 75 %;
      глубокосупоросные (за 7–10 дней до опороса) и подсосные 20 °C (18–22), RH ≤ 70 %;
      прим. 8: гнездо от 30 °C со снижением к отъёму до 22 °C, подогреваемый пол
      0.5–1.5 м² на станок.

Диапазон источника берётся серединой (21–23 → 22). Где источник даёт день
диапазоном («7–10»), — тоже середина.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from .genetics import NEUTRAL_ZONE_C, SetpointMeaning

SowStage = Literal["dry", "gestation", "lactation"]

Curve = tuple[tuple[float, float], ...]

# Отъём по умолчанию для кривой гнезда РД-АПК (30 → 22 °C «к отъёму»): срок отъёма
# в норме не задан — 28 дней (HYPOTHESIS), передаётся параметром
DEFAULT_WEANING_DAY = 28.0


@dataclass(frozen=True)
class SowProfile:
    id: str
    name: str
    source_id: str
    setpoint_meaning: SetpointMeaning
    dry_c: Optional[float]
    gestation_c: Optional[float]
    lactation_room: Curve                      # (день лактации, °C)
    lactation_room_covered_nest: Optional[Curve] = None
    nest: Optional[Curve] = None               # (день лактации, °C) — середина диапазона
    rh_max_pct: Optional[float] = None
    # Вентиляция на свиноматку, м³/ч: стадия → (мин., макс.); лактация — кривая по дням
    vent_dry: Optional[tuple[float, float]] = None
    vent_gestation: Optional[tuple[float, float]] = None
    vent_lactation_min: Optional[Curve] = None
    vent_lactation_max: Optional[Curve] = None
    nest_ends_at_weaning: bool = False         # кривая гнезда идёт до дня отъёма (РД-АПК)


def _lerp(x: float, curve: Curve) -> float:
    if x <= curve[0][0]:
        return curve[0][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return curve[-1][1]


SOW_PROFILES: dict[str, SowProfile] = {p.id: p for p in (
    SowProfile(
        id="pic_2021", name="PIC Gilt & Sow 2021", source_id="pic_2021_sow",
        setpoint_meaning="room_temperature", dry_c=19.0, gestation_c=19.0,
        lactation_room=((0.0, 22.0), (8.5, 19.0)),
        nest=((0.0, 33.5), (28.0, 33.5)),
        rh_max_pct=65.0,
    ),
    SowProfile(
        id="hypor_2023", name="Hypor Libra Farrowing 2.0", source_id="hypor_libra_2023",
        setpoint_meaning="room_temperature", dry_c=None, gestation_c=None,
        # «вокруг опороса» — день 0; «1-я неделя» — середина недели; «до отъёма» — со 2-й недели
        lactation_room=((0.0, 23.5), (3.5, 22.5), (14.0, 20.5)),
        nest=((0.0, 34.5), (3.5, 32.5), (14.0, 30.5)),
        rh_max_pct=75.0,
    ),
    SowProfile(
        id="klimaatplatform_2021", name="Klimaatplatform NL 2021 / Topigs Norsvin",
        source_id="klimaatplatform_2021", setpoint_meaning="ventilation_start",
        dry_c=20.0, gestation_c=20.0,
        # до опороса (−3) 20; при опоросе (0) 23; через неделю (7) 20; конец периода (25) 20
        lactation_room=((-3.0, 20.0), (0.0, 23.0), (7.0, 20.0), (25.0, 20.0)),
        lactation_room_covered_nest=((-3.0, 20.0), (0.0, 23.0), (7.0, 18.0), (25.0, 18.0)),
        nest=((1.0, 34.0), (7.0, 30.0), (25.0, 24.5)),
        vent_dry=(18.0, 150.0), vent_gestation=(25.0, 150.0),
        vent_lactation_min=((-3.0, 25.0), (0.0, 25.0), (7.0, 35.0), (25.0, 60.0)),
        vent_lactation_max=((-3.0, 250.0), (25.0, 250.0)),
    ),
    SowProfile(
        id="rd_apk_2012", name="РД-АПК 1.10.02.04-12 (норма РФ)", source_id="rd_apk_2012",
        setpoint_meaning="room_temperature", dry_c=16.0, gestation_c=16.0,
        lactation_room=((-7.0, 20.0), (28.0, 20.0)),
        nest=((0.0, 30.0), (DEFAULT_WEANING_DAY, 22.0)),
        rh_max_pct=70.0, nest_ends_at_weaning=True,
    ),
)}


def _note(code: str, **params) -> dict:
    return {"code": code, "params": params} if params else {"code": code}


@dataclass(frozen=True)
class SowSetpoints:
    profile_id: str
    stage: str
    room_setpoint_c: float
    setpoint_meaning: str
    design_room_temp_c: float
    nest_c: Optional[float]
    min_vent_m3h: Optional[float]
    max_vent_m3h: Optional[float]
    rh_pct: Optional[float]
    source_id: str
    assumptions: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


def sow_setpoints(profile_id: str, stage: SowStage, lactation_day: Optional[float] = None,
                  covered_nest: bool = False, weaning_day: float = DEFAULT_WEANING_DAY) -> SowSetpoints:
    p = SOW_PROFILES[profile_id]
    assumptions: list[dict] = []
    warnings: list[dict] = []
    nest = vmin = vmax = None

    if stage == "lactation":
        if lactation_day is None:
            raise ValueError("lactation_day is required for stage 'lactation' (−3 — до опороса)")
        room_curve = p.lactation_room_covered_nest if covered_nest and p.lactation_room_covered_nest else p.lactation_room
        room = _lerp(lactation_day, room_curve)
        if p.nest:
            nest_curve = p.nest
            if p.nest_ends_at_weaning:
                nest_curve = ((p.nest[0][0], p.nest[0][1]), (weaning_day, p.nest[-1][1]))
                if weaning_day == DEFAULT_WEANING_DAY:
                    assumptions.append(_note("weaning_day_default", day=DEFAULT_WEANING_DAY))
            nest = round(_lerp(lactation_day, nest_curve), 1) if lactation_day >= 0 else None
        if p.vent_lactation_min:
            vmin = round(_lerp(lactation_day, p.vent_lactation_min), 1)
        if p.vent_lactation_max:
            vmax = round(_lerp(lactation_day, p.vent_lactation_max), 1)
    else:
        room = p.dry_c if stage == "dry" else p.gestation_c
        if room is None:
            raise ValueError(f"profile {profile_id} has no {stage} room temperature")
        vent = p.vent_dry if stage == "dry" else p.vent_gestation
        if vent:
            vmin, vmax = vent

    if p.setpoint_meaning == "ventilation_start":
        design = round(room - NEUTRAL_ZONE_C, 1)
        assumptions.append(_note("heating_below_ventilation_start", neutral_zone_c=NEUTRAL_ZONE_C))
    else:
        design = round(room, 1)

    return SowSetpoints(
        profile_id=p.id, stage=stage, room_setpoint_c=round(room, 1), setpoint_meaning=p.setpoint_meaning,
        design_room_temp_c=design, nest_c=nest, min_vent_m3h=vmin, max_vent_m3h=vmax,
        rh_pct=p.rh_max_pct, source_id=p.source_id, assumptions=assumptions, warnings=warnings,
    )
