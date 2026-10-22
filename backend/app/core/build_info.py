"""
Версия запущенного приложения — чтобы по интерфейсу и /version было видно, какой
код стоит. Перенесено из MINMAXapp (backend/app/core/build_info.py) с одним отличием.

Раньше /version знал только RENDER_GIT_COMMIT и на внутреннем сервере (AGR Deploy,
git clone в /srv/pigmax) отвечал "local": нельзя было понять, обновилась ли копия.

Отличие от MINMAXapp: коммит читается прямо из файлов .git (HEAD → ref →
refs/heads или packed-refs), без запуска git. git отказывается работать в чужом
по владельцу репозитории («dubious ownership»), и если служба запущена не от
владельца /srv/pigmax, версия стала бы "unknown" — а скрипт обновления по ней
проверяет, что работает новый код, и откатил бы исправное обновление.
Дата и «грязное» дерево — по-прежнему через git: они справочные, без них версия
остаётся верной.
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional

# Семантическая версия продукта — меняется вручную на заметных выпусках.
# 0.2.0 — физическая модель тепла (этап 1)
APP_VERSION = "0.2.0"

_REPO = Path(__file__).resolve().parents[3]


def read_head_commit(repo: Path) -> Optional[str]:
    """Полный хеш HEAD из файлов .git или None (нет .git, повреждён)."""
    git_dir = repo / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head or None                     # отсоединённый HEAD — хеш прямо в файле
        ref = head[4:].strip()                      # refs/heads/main
        loose = git_dir / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip() or None
        # После git gc ссылки уходят в packed-refs: «<хеш> refs/heads/main»
        packed = git_dir / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except OSError:
        pass
    return None


def read_head_branch(repo: Path) -> Optional[str]:
    try:
        head = (repo / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return head.rsplit("/", 1)[-1] if head.startswith("ref: refs/heads/") else None


def _git(*args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(_REPO), *args], capture_output=True, text=True, timeout=3)
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


@lru_cache(maxsize=1)
def build_info() -> dict:
    # Один раз при старте: деплой всё равно перезапускает процесс
    commit = (os.environ.get("RENDER_GIT_COMMIT") or read_head_commit(_REPO) or "unknown")[:7]
    branch = os.environ.get("RENDER_GIT_BRANCH") or read_head_branch(_REPO)
    date = _git("log", "-1", "--format=%cs")
    # Незакоммиченные правки — только при локальной разработке; на площадке их быть не должно
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
    return {"version": APP_VERSION, "commit": commit + ("+dirty" if dirty else ""),
            "branch": branch, "date": date}
