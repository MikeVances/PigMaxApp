"""
Перенесено из MINMAXapp (backend/app/core/paths.py, 59390c7) — docs/porting/api_census.md.

Пути к файлам данных приложения.

Все пути строятся от корня проекта, а не от текущей рабочей директории:
раньше "data/day_master.json" при запуске из backend/ молча открывал
другой файл (backend/data/...), и расчёт шёл по другому профилю роста.

Переменные окружения (DAY_MASTER_JSON и т.п.) по-прежнему переопределяют путь.
Относительное значение в env тоже резолвится от корня проекта — по той же причине.
"""

from __future__ import annotations

import os
from pathlib import Path

# backend/app/core/paths.py → parents[3] = корень репозитория
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"


def data_path(env_var: str, default_relative: str) -> Path:
    # Source: core/paths.py -> data_path
    """
    Абсолютный путь к файлу данных.

    env_var          — имя переменной окружения, которая переопределяет путь
    default_relative — путь по умолчанию относительно корня проекта
    """
    raw = os.getenv(env_var) or default_relative
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()
