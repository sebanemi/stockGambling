"""Secret-hygiene tests.

These are cheap, permanent guards: no credential may ever reach the
repository, a log line, or the API response.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.session import _redact

pytestmark = pytest.mark.unit

SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|passwd)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"
)

#: Placeholders that are obviously not real credentials.
ALLOWLIST = {"change-me-local-only", "example", "changeme", "placeholder"}

#: File types that can plausibly contain a credential.
SCANNED_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".yml", ".yaml", ".toml", ".ini", ".sql"}

#: Markers that identify the repository root.
ROOT_MARKERS = (".gitignore", "docker-compose.yml", ".env.example")


def find_repo_root(start: Path) -> Path | None:
    """Walk upwards looking for the repository root.

    Returns ``None`` when only a partial source tree is available, which is the
    case inside the backend Docker image (it ships ``app/`` and ``tests/`` only).
    """
    for candidate in [start, *start.parents]:
        if all((candidate / marker).exists() for marker in ROOT_MARKERS):
            return candidate
    return None


REPO_ROOT = find_repo_root(Path(__file__).resolve().parent)

requires_repo = pytest.mark.skipif(
    REPO_ROOT is None,
    reason="repository root not available (partial source tree, e.g. inside the API image)",
)


def _candidate_files(root: Path) -> list[Path]:
    """Return every source file in the repository worth scanning."""
    skip_dirs = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".next",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    }
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        files.append(path)
    return files


@requires_repo
def test_no_hardcoded_secrets_in_repository() -> None:
    """No source file may embed a literal credential."""
    assert REPO_ROOT is not None
    offenders: list[str] = []
    for path in _candidate_files(REPO_ROOT):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in SECRET_PATTERN.finditer(text):
            value = match.group(0).split("=", 1)[-1].strip("'\"")
            if value.lower() in ALLOWLIST:
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT)}: {match.group(0)[:60]}")
    assert not offenders, "Possible hardcoded secrets:\n" + "\n".join(offenders)


@requires_repo
def test_env_file_is_not_committed() -> None:
    """`.env` is git-ignored, and git does not track any `.env` file.

    A local `.env` is *expected* to exist on developer machines (it is created
    from `.env.example`); what must never happen is it being committed.
    """
    assert REPO_ROOT is not None
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore
    assert (REPO_ROOT / ".env.example").exists()

    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout; nothing to verify against `git ls-files`")

    tracked = subprocess.run(  # noqa: S603
        ["git", "-C", str(REPO_ROOT), "ls-files"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    offenders = [name for name in tracked if Path(name).name == ".env"]
    assert not offenders, f"Tracked secret files: {offenders}"


def test_dsn_is_redacted_before_logging() -> None:
    """The password is masked in any DSN that reaches a log record."""
    redacted = _redact("postgresql+psycopg://stockgambling:supersecret@db:5432/sg")
    assert "supersecret" not in redacted
    assert "***" in redacted
    assert redacted.endswith("@db:5432/sg")


def test_api_never_exposes_credentials(client: TestClient, settings: Settings) -> None:
    """No public endpoint may return the database password or Redis secret."""
    for path in ("/health", "/health/live", "/health/ready", settings.api_prefix, "/openapi.json"):
        payload = client.get(path).text
        assert settings.postgres_password not in payload
        if settings.redis_password:
            assert settings.redis_password not in payload
