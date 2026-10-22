"""
Общая подготовка тестов.

Окружение выставляется на верхнем уровне conftest — до того, как какой-либо
тест импортирует backend.app: так рабочий backend/data/pig_settings.json
никогда не читается и не перезаписывается тестами.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_DATA_DIR = Path(tempfile.mkdtemp(prefix="pigmax_tests_"))
SETTINGS_FILE = _DATA_DIR / "pig_settings.json"
os.environ["PIG_SETTINGS_JSON"] = str(SETTINGS_FILE)

# main.py при импорте грузит backend/.env. load_dotenv не перезаписывает уже
# заданные переменные — поэтому пустые строки, а не удаление: иначе dotenv
# подставил бы настоящий токен, и тест обратной связи написал бы в рабочий чат
for _var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "WRITE_TOKEN"):
    os.environ[_var] = ""

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import app  # noqa: E402
from backend.app.routers import feedback  # noqa: E402


@pytest.fixture
def settings_file() -> Path:
    # Путь отдаём фикстурой, а не импортом: pytest грузит conftest под своим
    # именем, и «from backend.tests.conftest import …» выполнил бы модуль
    # второй раз — со вторым временным каталогом и вторым PIG_SETTINGS_JSON
    return SETTINGS_FILE


@pytest.fixture
def client(monkeypatch):
    # Каждый тест начинает с чистых настроек и без токена записи
    monkeypatch.delenv("WRITE_TOKEN", raising=False)
    SETTINGS_FILE.unlink(missing_ok=True)
    # Счётчик лимита обратной связи общий на процесс — иначе тесты зависели бы от порядка
    feedback._recent.clear()
    with TestClient(app) as c:
        yield c
    SETTINGS_FILE.unlink(missing_ok=True)
