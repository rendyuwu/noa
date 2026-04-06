from __future__ import annotations

from pathlib import Path

from noa_api.core.config import resolve_env_file


def test_resolve_env_file_prefers_repo_root_env(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "AGENTS.md").write_text("# repo", encoding="utf-8")
    repo_env_file = repo_root / ".env"
    repo_env_file.write_text("ENVIRONMENT=test", encoding="utf-8")

    start = repo_root / "apps" / "api" / "src" / "noa_api" / "core" / "config.py"

    assert resolve_env_file(start=start, cwd=tmp_path / "cwd") == repo_env_file


def test_resolve_env_file_falls_back_to_cwd_env(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "AGENTS.md").write_text("# repo", encoding="utf-8")

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    cwd_env_file = cwd / ".env"
    cwd_env_file.write_text("ENVIRONMENT=test", encoding="utf-8")

    start = repo_root / "apps" / "api" / "src" / "noa_api" / "core" / "config.py"

    assert resolve_env_file(start=start, cwd=cwd) == cwd_env_file


def test_resolve_env_file_returns_none_when_missing(tmp_path: Path) -> None:
    start = (
        tmp_path
        / "workspace"
        / "apps"
        / "api"
        / "src"
        / "noa_api"
        / "core"
        / "config.py"
    )
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    assert resolve_env_file(start=start, cwd=cwd) is None
