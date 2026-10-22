"""
Перенесено из MINMAXapp (backend/app/core/construction.py, 59390c7) по переписи
docs/porting/house_census.md.

Справочник материалов ограждений и UA оболочки по слоям — для проектного расчёта
(API v1, сценарий «проект»), когда корпуса ещё нет и тест остывания не сделать.

Метод — ISO 6946: R_total = R_si + Σ d/λ + R_se, U = 1 / R_total.
Источник значений — BRE BR 443 «Conventions for U-value calculations», 2019
(открытый документ, CIBSE): §3 — расчётная теплопроводность по BS EN ISO 10456 и
CIBSE Guide A, §4.3 — сопротивления теплоотдаче по BS EN ISO 6946. Сам ISO 10456
платный, поэтому ссылаемся на документ, который его значения воспроизводит.

Утеплители (минвата, EPS, XPS) в справочник НЕ входят: BR 443 §3 прямо велит брать
заявленную λ производителя (DoP) — у минваты она от 0.034 до 0.044 в зависимости
от плотности, «типовое» значение здесь было бы выдумкой. Такой слой передаётся с
λ из паспорта (Layer.lambda_w_mk). Исключение — ядро PIR сэндвич-панели: одно
значение из реальной декларации, с пометкой «типовое».

Сэндвич-панель лучше задавать готовым U из декларации (EN 14509): он учитывает
стыки и крепёж. Пример — та же DoP: PIR 40 мм, λD = 0.022, Ud,S = 0.57, а по
слоям выходит ≈ 0.50 — разница 13 % и есть стыки.

Пол и утечки сюда не входят: пол — теплообмен через грунт и массу (ISO 13370,
другой метод), утечки — house.airtightness (leakage_calc).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .building_thermal import U_ENVELOPE_MAX_W_M2K, U_ENVELOPE_MIN_W_M2K
from .errors import CalcError


# ─── Источники ────────────────────────────────────────────────────────────────

# Источники (`source_id`) с пересказом и ограничениями — в core/sources.py:
# там же, где их читает справочник API /sources


# ─── Материалы ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Material:
    id: str
    name: str                         # для журналов и справочника; перевод — на клиенте по id
    lambda_w_mk: float                # расчётная теплопроводность, Вт/(м·К)
    source_id: str
    density_kg_m3: Optional[float] = None
    typical_thickness_mm: tuple[float, ...] = ()


# Значения — дословно из BR 443 §3. Кирпич и раствор там даны для наружной и
# внутренней верст: наружная влажнее (дождь), поэтому λ выше. Однослойная стена
# свинарника — это «наружная верста»; «внутренняя» — для облицовки изнутри.
_MATERIALS: tuple[Material, ...] = (
    Material("concrete_reinforced", "Reinforced concrete (1% steel)", 2.30, "br443_2019_s3",
             typical_thickness_mm=(120, 150, 200)),
    Material("concrete_screed_1800", "Concrete / screed, 1800 kg/m³", 1.15, "br443_2019_s3",
             density_kg_m3=1800),
    Material("brick_exposed", "Clay brick, exposed (outer leaf)", 0.77, "br443_2019_s3",
             typical_thickness_mm=(120, 250, 380)),
    Material("brick_protected", "Clay brick, protected (inner leaf)", 0.56, "br443_2019_s3",
             typical_thickness_mm=(120, 250)),
    Material("mortar_exposed", "Mortar / render, exposed", 0.94, "br443_2019_s3",
             typical_thickness_mm=(10, 20)),
    Material("mortar_protected", "Mortar / plaster, protected", 0.88, "br443_2019_s3",
             typical_thickness_mm=(10, 20)),
    Material("timber_softwood", "Softwood timber, 500 kg/m³", 0.13, "br443_2019_s3",
             density_kg_m3=500, typical_thickness_mm=(25, 40, 50)),
    Material("timber_hardwood", "Hardwood timber, 700 kg/m³", 0.18, "br443_2019_s3",
             density_kg_m3=700),
    Material("plywood", "Plywood, 500 kg/m³", 0.13, "br443_2019_s3",
             density_kg_m3=500, typical_thickness_mm=(12, 15, 18)),
    Material("osb", "OSB, 650 kg/m³", 0.13, "br443_2019_s3",
             density_kg_m3=650, typical_thickness_mm=(9, 12, 15)),
    Material("particleboard", "Particleboard / fibreboard, 600 kg/m³", 0.14, "br443_2019_s3",
             density_kg_m3=600),
    Material("gypsum_board", "Gypsum plasterboard, ≤700 kg/m³", 0.21, "br443_2019_s3",
             density_kg_m3=700, typical_thickness_mm=(12.5,)),
    Material("steel", "Mild / galvanised steel", 50.0, "br443_2019_s3",
             typical_thickness_mm=(0.5, 0.6, 0.7)),
    Material("aluminium", "Aluminium alloy", 160.0, "br443_2019_s3"),
    Material("pir_core", "PIR/PUR sandwich panel core (typical DoP)", 0.022,
             "marcegaglia_dop_100p_2022", typical_thickness_mm=(60, 80, 100, 120, 150)),
)

MATERIALS: dict[str, Material] = {m.id: m for m in _MATERIALS}


# ─── Сопротивления теплоотдаче (BR 443 §4.3 / ISO 6946) ──────────────────────

# Поток тепла зимой: через стену — горизонтально, через кровлю — вверх. У кровли
# R_si меньше (тёплый воздух у потолка отдаёт тепло лучше), значит U кровли
# выше, чем у стены тех же слоёв.
R_SE_M2K_W = 0.04
R_SI_HORIZONTAL_M2K_W = 0.13
R_SI_UPWARD_M2K_W = 0.10

# «Горизонтальный» поток — в пределах ±30° от горизонтали (BR 443 §4.3): скат
# круче 60° работает как стена
_STEEP_ROOF_DEG = 60.0


def surface_resistance_si(element: str, roof_pitch_deg: float = 0.0) -> float:
    # Source: core/construction.py -> surface_resistance_si
    """R_si для стены или кровли с учётом уклона, м²·К/Вт."""
    if element == "roof" and roof_pitch_deg <= _STEEP_ROOF_DEG:
        return R_SI_UPWARD_M2K_W
    return R_SI_HORIZONTAL_M2K_W


# ─── Слои и U ─────────────────────────────────────────────────────────────────

# Границы λ для слоя с паспортной теплопроводностью: ниже 0.01 нет даже у
# вакуумных панелей, выше 200 — только чистые металлы, которые здесь не нужны
_LAMBDA_MIN, _LAMBDA_MAX = 0.01, 200.0


@dataclass(frozen=True)
class Layer:
    """Слой ограждения: материал из справочника ИЛИ λ из паспорта продукта."""
    thickness_mm: float
    material_id: Optional[str] = None
    lambda_w_mk: Optional[float] = None


@dataclass(frozen=True)
class Surface:
    """Конструкция: слои снаружи внутрь ИЛИ готовый U из декларации производителя."""
    layers: tuple[Layer, ...] = ()
    u_w_m2k: Optional[float] = None


def layer_resistance(layer: Layer) -> float:
    # Source: core/construction.py -> layer_resistance
    """R слоя = d / λ, м²·К/Вт."""
    if layer.thickness_mm <= 0:
        raise CalcError("validation_failed", field="thickness_mm")
    # Ровно один источник λ: оба сразу — неясно, чему верить; ни одного — нечем считать
    if (layer.material_id is None) == (layer.lambda_w_mk is None):
        raise CalcError("validation_failed", field="material_id|lambda_w_mk")
    if layer.material_id is not None:
        material = MATERIALS.get(layer.material_id)
        if material is None:
            raise CalcError("material_not_found", id=layer.material_id)
        lam = material.lambda_w_mk
    else:
        lam = layer.lambda_w_mk
        if not (_LAMBDA_MIN <= lam <= _LAMBDA_MAX):
            raise CalcError("validation_failed", field="lambda_w_mk")
    return layer.thickness_mm / 1000.0 / lam


def surface_u(surface: Surface, element: str, roof_pitch_deg: float = 0.0) -> float:
    # Source: core/construction.py -> surface_u
    """U конструкции, Вт/(м²·К): из декларации как есть или 1 / (R_si + Σ d/λ + R_se)."""
    if surface.u_w_m2k is not None:
        # U из декларации уже включает сопротивления теплоотдаче и стыки
        return surface.u_w_m2k
    if not surface.layers:
        raise CalcError("validation_failed", field="layers")
    r_total = (surface_resistance_si(element, roof_pitch_deg)
               + sum(layer_resistance(layer) for layer in surface.layers)
               + R_SE_M2K_W)
    return 1.0 / r_total


# ─── Геометрия и UA оболочки ──────────────────────────────────────────────────

@dataclass(frozen=True)
class HouseGeometry:
    length_m: float
    width_m: float
    wall_height_m: float
    ridge_height_m: Optional[float] = None    # нет — плоская кровля


@dataclass(frozen=True)
class EnvelopeAreas:
    side_walls_m2: float
    end_walls_m2: float      # вместе с фронтонами
    roof_m2: float           # по скатам, а не в плане
    roof_pitch_deg: float

    @property
    def total_m2(self) -> float:
        # Source: core/construction.py -> EnvelopeAreas.total_m2
        return self.side_walls_m2 + self.end_walls_m2 + self.roof_m2


def envelope_areas(g: HouseGeometry) -> EnvelopeAreas:
    # Source: core/construction.py -> envelope_areas
    """
    Площади оболочки двускатного корпуса. Для плоской кровли совпадает с
    building_thermal.envelope_area_m2 — тот же корпус, та же площадь.
    """
    rise = max(0.0, (g.ridge_height_m or g.wall_height_m) - g.wall_height_m)
    half_span = g.width_m / 2.0
    slope_len = math.hypot(half_span, rise)
    return EnvelopeAreas(
        side_walls_m2=2.0 * g.length_m * g.wall_height_m,
        # Торец — прямоугольник до карниза плюс треугольник фронтона
        end_walls_m2=2.0 * (g.width_m * g.wall_height_m + half_span * rise),
        roof_m2=2.0 * g.length_m * slope_len,
        roof_pitch_deg=math.degrees(math.atan2(rise, half_span)),
    )


@dataclass(frozen=True)
class EnvelopeUA:
    ua_w_per_k: float
    side_walls_u: float
    end_walls_u: float
    roof_u: float
    areas: EnvelopeAreas

    @property
    def mean_u_w_m2k(self) -> float:
        # Source: core/construction.py -> EnvelopeUA.mean_u_w_m2k
        return self.ua_w_per_k / self.areas.total_m2


def envelope_ua(g: HouseGeometry, walls: Surface, roof: Surface,
                end_walls: Optional[Surface] = None) -> EnvelopeUA:
    # Source: core/construction.py -> envelope_ua
    """
    UA оболочки (стены, торцы, кровля), Вт/К. Торцы без своей конструкции — как стены.

    Средний U проверяется тем же коридором, что калибровка теста остывания
    (building_thermal.U_ENVELOPE_*): опечатка в толщине (мм вместо см) даёт U вне
    физики, и такой проект не должен молча уйти в подбор горелок.
    """
    areas = envelope_areas(g)
    u_side = surface_u(walls, "wall")
    u_end = surface_u(end_walls, "wall") if end_walls is not None else u_side
    u_roof = surface_u(roof, "roof", areas.roof_pitch_deg)
    ua = u_side * areas.side_walls_m2 + u_end * areas.end_walls_m2 + u_roof * areas.roof_m2

    result = EnvelopeUA(ua_w_per_k=ua, side_walls_u=u_side, end_walls_u=u_end,
                        roof_u=u_roof, areas=areas)
    mean_u = result.mean_u_w_m2k
    if not (U_ENVELOPE_MIN_W_M2K <= mean_u <= U_ENVELOPE_MAX_W_M2K):
        raise CalcError("envelope_implausible", u=round(mean_u, 2),
                        min=U_ENVELOPE_MIN_W_M2K, max=U_ENVELOPE_MAX_W_M2K)
    return result
