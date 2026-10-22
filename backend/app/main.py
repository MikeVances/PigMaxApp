from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .routers import pig_calc
from .routers import pig_settings as settings_router
from .routers import feedback as feedback_router
from .core.build_info import APP_VERSION, build_info

# Ищем .env рядом с backend/ независимо от рабочей директории запуска
load_dotenv(Path(__file__).parent.parent / ".env")

app = FastAPI(title="PigMax — Pig House Ventilation", version=APP_VERSION)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/version")
def version():
    # Коммит, ветка, дата: на внутреннем сервере — из .git, на Render — из его переменных
    return build_info()


app.include_router(pig_calc.router,        tags=["calc"])
app.include_router(settings_router.router, tags=["settings"])
app.include_router(feedback_router.router, tags=["feedback"])

_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="ui")
