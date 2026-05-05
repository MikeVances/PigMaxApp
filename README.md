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
Q_pigs         = 5.09 × W^0.75 × N                (W)   ← Brown-Brandl 2004
```

Where:
- `UA` — building envelope heat transfer coefficient (W/K)
- `V̇_min` — minimum ventilation rate (m³/h), from PIC Table 4.1 × head count
- `W` — average live weight (kg), `N` — number of pigs
- `5.09` — pig heat production coefficient (2012 updated estimate)

**Why is the coefficient different from poultry?**  
Pigs produce less heat per unit of metabolic mass than broilers at the same weight. The `W^0.75` scaling (Kleiber's law) applies to both species, but the proportionality constant differs because of metabolic rate, insulation (skin vs. feathers), and body composition differences.

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

---

## Data Sources

| Source | Used for |
|--------|---------|
| PIC Grow-Finish Management Guide, 2019 | Temperature setpoints, min ventilation, stocking density, gas limits |
| Brown-Brandl T.M. et al., ASAE Transactions 47(1):259-270, 2004 | Pig heat production model |
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
