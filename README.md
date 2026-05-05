# 🐷 PigMaxApp — Pig House Ventilation Calculator

Web application for calculating ventilation parameters in pig houses (post-weaning & finishing).  
Based on PIC 2019 Management Guide and Brown-Brandl thermal standards.

---

## Features

- **Ventilation setpoints** — target temperature and min airflow by live weight (PIC Table 4.1)
- **Heat balance** — pig heat production vs ventilation/wall losses, heater duty cycle
- **Stocking density check** — floor area per head vs PIC Table 5.1 norms
- **Building settings** — dimensions, heater power, UA coefficient
- **Gas limits** — NH₃, CO₂, CO, H₂S reference values for pig houses
- **5 languages** — EN / RU / PL / DE / FR with native pig industry terminology
- **Feedback** — in-app form → Telegram bot

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (Python 3.10+) |
| Data storage | JSON files |
| Frontend | Vanilla JS + CSS custom properties |
| i18n | Synchronous `window.LOCALES` engine |
| Deploy | Render (free tier) |

## Physics

- Pig heat: `Q = 5.09 × W^0.75` W/head (Brown-Brandl et al., ASAE 2004)
- Ventilation heat loss: `Q = V̇_min × 0.335 × ΔT`
- Temperature/ventilation setpoints: PIC 2019 Table 4.1 (interpolated by live weight)

## Quick Start

```bash
cd backend
pip install .
uvicorn app.main:app --reload --port 8000
# Open http://localhost:8000
```

## Environment Variables

```bash
cp backend/.env.example backend/.env
# Fill in:
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

## Deploy to Render

| Setting | Value |
|---------|-------|
| Root Directory | `backend` |
| Build Command | `pip install .` |
| Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in Render → Environment.

## Data Sources

- PIC Руководство по работе на доращивании и откорме, 2019
- Brown-Brandl T.M. et al. — Heat and moisture production of swine, ASAE Transactions 47(1):259-270, 2004

## Related

- [MINMAXapp](https://github.com/MikeVances/MINMAXapp) — Broiler house ventilation calculator

## License

MIT — open source, free to use and adapt.
