"""
# Source: MINMAXapp backend/app/core/json_store.py (59390c7) — PORT 1:1
Надёжная запись JSON-файлов данных (настройки, теплотехника, профили).

Обрыв посреди записи. open(path, "w") сначала обнуляет файл; если процесс
   упадёт или диск переполнится во время json.dump, на диске останется обрезанный
   JSON, и при следующем чтении данные потеряны целиком. Поэтому пишем во
   временный файл рядом и подменяем им старый через os.replace — это атомарно:
   читатель видит либо старую версию, либо новую.

Все потребители пишут файл целиком (настройки, конфигурация калибровки, профиль),
цикла «прочитать → изменить → записать» нет, поэтому блокировок на файл здесь нет.
Если такой цикл появится — блокировку заводить здесь же, по пути файла.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: str | os.PathLike, data: Any) -> None:
    # Source: core/json_store.py -> atomic_write_json
    """Записать JSON через временный файл в том же каталоге + os.replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Временный файл в ТОМ ЖЕ каталоге: os.replace атомарен только в пределах
    # одной файловой системы
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
