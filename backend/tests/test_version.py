"""
Метка версии (2026-10-22): /version отвечал "local" везде, кроме Render, и по
внутреннему серверу нельзя было понять, обновился ли он.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from backend.app.core import build_info as bi

REPO = Path(__file__).resolve().parents[2]


def _git_head() -> str:
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_version_reports_the_checked_out_commit(client, monkeypatch):
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    bi.build_info.cache_clear()
    try:
        v = client.get("/version").json()
    finally:
        bi.build_info.cache_clear()
    assert v["commit"].startswith(_git_head()[:7])
    assert v["commit"] != "local"
    assert v["version"] == bi.APP_VERSION


def test_render_variable_wins(client, monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abcdef0123456789")
    bi.build_info.cache_clear()
    try:
        assert client.get("/version").json()["commit"].startswith("abcdef0")
    finally:
        bi.build_info.cache_clear()


def test_head_read_from_files_matches_git():
    # Без запуска git: служба может работать не от владельца репозитория
    assert bi.read_head_commit(REPO) == _git_head()


def test_packed_refs_and_detached_head(tmp_path):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "packed-refs").write_text("# pack-refs with: peeled\n1111111222222 refs/heads/main\n")
    assert bi.read_head_commit(tmp_path) == "1111111222222"
    assert bi.read_head_branch(tmp_path) == "main"
    (git / "HEAD").write_text("3333333444444\n")
    assert bi.read_head_commit(tmp_path) == "3333333444444"
    assert bi.read_head_branch(tmp_path) is None


def test_no_git_dir_is_unknown_not_a_crash(tmp_path):
    assert bi.read_head_commit(tmp_path) is None
