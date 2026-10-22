# 🐷 PigMaxApp — Pig House Ventilation Calculator

Open-source web tool for pig farm engineers and production managers.  
Calculates ventilation setpoints, heat balance, and stocking density for post-weaning and finishing houses — based on PIC 2019 recommendations and peer-reviewed thermal models.

> Companion app to [MINMAXapp](https://github.com/MikeVances/MINMAXapp) (broiler houses). Same approach, different species.

---

## What it does

### Ventilation Setpoints
Enter live weight and outside temperature — get the target temperature setpoint and minimum ventilation rate per head, interpolated from PIC 2019 Table 4.1. Covers the full grow-out from weaning (5 kg) to heavy finishing (147 kg).

### Heat Balance
Full thermal analysis of the pig house:

- Total heat produced by the herd
- Heat lost through minimum ventilation
- Heat lost through building envelope (walls, roof, floor)
- Required heating power and estimated heater duty cycle

### Stocking Density Check
Calculates actual floor area per head and compares against PIC 2019 Table 5.1 norms for slatted and solid floors.

### Gas Concentration Reference
Displays PIC 2019 maximum allowable concentrations for pig house air quality:
NH₃, CO₂, CO, H₂S — critical parameters for animal welfare and worker safety.

---

## Physics

```
Q_heater = Q_walls + Q_ventilation − Q_pigs

Q_walls        = UA × ΔT                          (W)
Q_ventilation  = V̇_min × 0.335 × ΔT              (W)
Q_pigs         = sensible heat × N                (W)   ← CIGR 2002 (core/pig_heat.py)
```

Where:
- `UA` — building envelope heat transfer coefficient (W/K)
- `V̇_min` — minimum ventilation rate (m³/h), from PIC Table 4.1 × head count
- `W` — average live weight (kg), `N` — number of pigs
- Q_pigs counts only **sensible** heat at house level: latent heat leaves with vapour and does not warm the air

**Heat production = maintenance + feed.** CIGR 2002 gives total heat as maintenance
(`5.09·m^0.75` for growing pigs) plus the part of the feed energy above maintenance that
turns into heat. Maintenance alone — what this app used before 2026-10 — is ~70 % of
the total for a 100 kg finisher. Three methods are available (CIGR 2002, Brown-Brandl
2004, Russian RD-APK 1.10.02.04-12); every response names the one it used.

---

## Interface

- Single-page app — works offline after first load
- **5 languages**: 🇬🇧 EN · 🇷🇺 RU · 🇵🇱 PL · 🇩🇪 DE · 🇫🇷 FR  
  Translations use native pig industry terminology (obsada, Mastdurchgang, bande, etc.)
- Inline feedback form → Telegram

---

## Run locally

```bash
git clone https://github.com/MikeVances/PigMaxApp
cd PigMaxApp/backend
pip install .
uvicorn app.main:app --reload
# → http://localhost:8000
```

Requires Python 3.10+. No database, no external services needed for core functionality.

### Tests

```bash
pip install pytest httpx
python -m pytest          # from the repository root
```

Tests never touch `backend/data/`: settings go to a temporary file via `PIG_SETTINGS_JSON`.

### Environment

| Variable | Purpose |
|---|---|
| `WRITE_TOKEN` | If set, `PUT /settings` requires header `X-Write-Token` (the UI asks once). Unset — writes are open |
| `PIG_SETTINGS_JSON` | Path to the settings file (default `backend/data/pig_settings.json`) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Feedback form delivery |
| `TRUSTED_PROXY_HOPS` | Feedback allows 5 messages per 10 minutes per client. Number of `X-Forwarded-For` entries your own proxies append (`1` on Render — set in `render.yaml`). `0` (default) ignores the header: without a proxy the client writes it |

References used by the calculator: [`docs/sources.md`](docs/sources.md).

### Heat production endpoint

`POST /calc/heat-production` — heat, vapour and CO₂ of one animal for any group
(`growing`, `boar`, `dry_sow`, `gestating_sow`, `lactating_sow`) by method
`cigr2002` (default), `brown_brandl_2004` (growing pigs only) or `rd_apk_2012`.
`GET /sources/{source_id}` explains every source id a response cites.

`POST /calc/min-ventilation` — minimum ventilation per head for any group as the largest of three
criteria: CO₂ dilution (CIGR 2002), moisture removal at the RH limit (psychrometric balance) and a
norm (`pic`, `rd_apk_2012` or `none`). The response names the deciding criterion (`basis`).
`/calc/ventilation` uses the same maximum instead of the PIC table alone.

---

## Data Sources

| Source | Used for |
|--------|---------|
| PIC Grow-Finish Management Guide, 2019 | Temperature setpoints, min ventilation, stocking density, gas limits |
| CIGR 4th Report (Pedersen & Sällvik, eds.), 2002 | Heat, vapour and CO₂ of all pig groups (default method) |
| Brown-Brandl T.M. et al., ASAE Transactions 47(1):259-270, 2004 | Heat production of modern growing pigs (alternative method) |
| РД-АПК 1.10.02.04-12 (Russia), 2012 | Heat, vapour and CO₂ tables (alternative method) |
| Huynh T.T.T. et al., 2005 | Humidity × temperature interaction on feed intake |
| EN 13779 / ASHRAE | Air properties, psychrometrics |

---

## Contributing

Issues and pull requests are welcome. If you work in pig production and notice a discrepancy between app output and actual farm conditions — open an issue. Real-world corrections from practitioners are the most valuable contribution.

---

## Related

- [MINMAXapp](https://github.com/MikeVances/MINMAXapp) — same tool for broiler houses

---

## License

MIT — use freely, adapt for your farm or software.
