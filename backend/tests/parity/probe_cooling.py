"""
Паритет калибровки UA по кривой остывания с MINMAXapp 59390c7 (cooling_fit +
calibration_problem). run(F, B): F — cooling_fit, B — building_thermal.
Синтетические отметки — из самой модели при известном UA (2500 Вт/К): подгонка
обязана его восстановить; два плохих теста — для веток предупреждений и ошибок.
"""
import dataclasses


def _taps(F, ua, k, c_struct, volume, t_out, t_start, step_c=1.0, n=6):
    c_air = volume * 1.2 * 1006.0
    curve = F.air_temperature_curve(ua, k, c_struct, c_air, t_start - t_out)
    pts, t, target = [(0.0, t_start)], 0.0, t_start - step_c
    while len(pts) <= n and t < 20000:
        t += 1.0
        if curve(t) + t_out <= target:
            pts.append((t, round(target, 2)))
            target -= step_c
    return pts


def run(F, B) -> dict:
    out = {}
    good = _taps(F, 2500, 9000, 6e7, 4800, -5.0, 20.0)
    out["good_taps"] = good
    out["good"] = dataclasses.asdict(F.fit_cooling_curve(4800, -5.0, good))
    warm = _taps(F, 2500, 9000, 6e7, 4800, 10.0, 20.0, step_c=0.5)
    out["small_dt"] = dataclasses.asdict(F.fit_cooling_curve(4800, 10.0, warm))
    for bad in ([(0, 20), (60, 19)], [(10, 20), (60, 19), (120, 18), (200, 17), (300, 16)],
                [(0, 20), (60, 19), (50, 18), (200, 17), (300, 16)]):
        try:
            F.fit_cooling_curve(4800, -5.0, bad)
            out.setdefault("errors", []).append(None)
        except Exception as e:
            out.setdefault("errors", []).append([type(e).__name__, getattr(e, "code", None)])
    bd = B.BuildingDimensions(100, 20, 2.4)
    for minutes, ua in ((5, 2500), (30, 2500), (30, 90000)):
        cal = B.CalibrationTest(20, 15, -5, minutes, ua, method="cooling_curve")
        out[f"problem_{minutes}_{ua}"] = B.calibration_problem(cal, bd)
    return out
