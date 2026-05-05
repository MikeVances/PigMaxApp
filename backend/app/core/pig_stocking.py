"""
pig_stocking.py — Stocking density recommendations.

Source: PIC 2019 Table 5.1 — Stocking density for market animals.
"""


def recommended_area_m2(weight_kg: float, floor_type: str = "slatted") -> float:
    """
    Minimum floor area per pig head, m².
    floor_type: 'slatted' | 'solid'
    """
    if floor_type == "slatted":
        if weight_kg < 27:
            return 0.26
        elif weight_kg <= 34:
            return 0.34
        elif weight_kg <= 120:
            return 0.68
        else:
            return 0.75
    else:  # solid floor
        if weight_kg <= 120:
            return 0.9
        else:
            return 1.0


def max_pigs_in_house(floor_area_m2: float, weight_kg: float, floor_type: str = "slatted") -> int:
    """Maximum number of pigs at given weight for given usable floor area."""
    area_per_head = recommended_area_m2(weight_kg, floor_type)
    return int(floor_area_m2 / area_per_head)


def stocking_check(num_pigs: int, floor_area_m2: float, weight_kg: float, floor_type: str = "slatted") -> dict:
    """
    Returns stocking density analysis.
    """
    area_per_head = recommended_area_m2(weight_kg, floor_type)
    actual_area   = floor_area_m2 / num_pigs if num_pigs > 0 else 0
    max_pigs      = max_pigs_in_house(floor_area_m2, weight_kg, floor_type)
    overstocked   = actual_area < area_per_head

    return {
        "recommended_m2_per_head": area_per_head,
        "actual_m2_per_head":      round(actual_area, 2),
        "max_pigs_recommended":    max_pigs,
        "overstocked":             overstocked,
        "utilization_pct":         round(num_pigs / max_pigs * 100, 1) if max_pigs > 0 else None,
    }
