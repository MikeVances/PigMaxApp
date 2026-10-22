"""
Паритет теста герметичности и помощников утечки/конденсата с MINMAXapp 59390c7.

run(L, H, C, B): L — leakage_calc, H — модуль с leak_at_4pa_m3h / coldest_surface /
condensation (в MINMAXapp — ventilation_design, у нас — house_checks), C —
construction, B — building_thermal. Эталон снят этой же функцией на оригинале.
"""
import dataclasses


def run(L, H, C, B) -> dict:
    out = {}
    out["leak_single"] = dataclasses.asdict(L.calculate_leakage(L.LeakageInput(96, 18, 36000, 30)))
    out["leak_multi"] = dataclasses.asdict(L.calculate_leakage(L.LeakageInput(
        96, 18, 36000, 30, extra_points=((24000, 15), (12000, 6)))))
    for p in (10, 25, 50, 80):
        out[f"fan_corr_{p}"] = L._fan_correction(30000, p)
    try:
        L.calculate_leakage(L.LeakageInput(96, 18, 36000, 30, extra_points=((35000, 31),)))
        out["too_close"] = None
    except Exception as e:
        out["too_close"] = [type(e).__name__, getattr(e, "code", None)]
    for cls in ("tight", "moderate", "leaky", "very_leaky"):
        out[f"q4_{cls}"] = list(H.leak_at_4pa_m3h(1728, H.Airtightness(tightness_class=cls)))
    out["q4_rel"] = list(H.leak_at_4pa_m3h(1728, H.Airtightness(relative_leakage=0.8)))
    out["q4_meas"] = list(H.leak_at_4pa_m3h(1728, H.Airtightness(leak_at_4pa_m3h=5000)))
    out["q4_default"] = list(H.leak_at_4pa_m3h(1728, None))
    g = C.HouseGeometry(96, 18, 2.6, 4.1)
    env = C.envelope_ua(g, C.Surface(u_w_m2k=0.35), C.Surface(u_w_m2k=0.5), C.Surface(u_w_m2k=1.9))
    out["coldest_env"] = list(H.coldest_surface(env, env.ua_w_per_k, g))
    out["coldest_mean"] = list(H.coldest_surface(None, 3000, g))
    for args in ((("roof", 0.5, 0.10), 18, 70, -20), (("x", 3.0, 0.13), 18, 70, -20), (("x", 3.0, 0.13), 5, 70, 10)):
        r = H.condensation(*args)
        out[f"cond_{args[0][0]}_{args[3]}"] = list(r) if r else None
    # Решатель горелок с полом обмена — уже в паритете; здесь — баланс с утечкой
    sol = B.solve_heater_duty(lambda d: 8000, 900, 18, -20, 40000, 300000, exchange_floor_m3h=15000)
    out["duty_floor"] = dataclasses.asdict(sol)
    return out
