"""Typed settings from the environment, optionally pre-loaded from ``backend/.env``.

A tiny parser avoids an extra dependency. Existing environment variables win
over the file, so tests and CI can override anything.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


load_env_file(BACKEND_DIR / ".env")


def _str(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    path = Path(raw) if raw else default
    return path if path.is_absolute() else BACKEND_DIR / path


def _optional_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else BACKEND_DIR / path


@dataclass(frozen=True)
class Settings:
    db_path: Path
    raw_dir: Path
    user_agent: str
    http_timeout: float
    http_max_retries: int
    gleif_rpm: int
    ai_provider: str
    ai_model: str
    ai_rpm_limit: int
    ai_rpm_safety_margin_seconds: float
    ai_max_concurrent_requests: int
    ai_max_retries: int
    ai_request_timeout: float
    ai_cache_ttl_seconds: int
    ai_daily_request_budget: int
    ai_daily_token_budget: int
    ai_queue_max: int
    ai_queue_max_wait_seconds: float
    ai_background_reserve: int
    ai_429_cooldown_seconds: float
    codex_token_file: Path
    codex_responses_url: str
    codex_reasoning_effort: str
    codex_account_id: str
    codex_originator: str
    codex_instructions_file: Path | None
    crawler_url: str
    crawler_connect_timeout: float
    crawler_read_timeout: float
    crawler_max_retries: int
    crawler_rpm: int
    crawler_daily_budget: int
    crawler_cache_ttl_seconds: int
    crawler_negative_ttl_seconds: int
    mca_source_url: str
    nse_source_url: str
    db_backend: str
    motherduck_token: str
    motherduck_database: str
    duckdb_path: Path
    cors_origins: str
    gcp_project_id: str
    bigquery_dataset: str
    bigquery_location: str
    gcs_bucket: str


def load_settings() -> Settings:
    return Settings(
        crawler_url=_str("CRAWLER_URL", "").rstrip("/"),
        crawler_connect_timeout=_float("CRAWLER_CONNECT_TIMEOUT", 10.0),
        # Render free-tier cold starts take ~50 s and one crawl does 2-4 upstream calls of up to 14 s each.
        crawler_read_timeout=_float("CRAWLER_READ_TIMEOUT", 90.0),
        crawler_max_retries=_int("CRAWLER_MAX_RETRIES", 1),
        crawler_rpm=_int("CRAWLER_RPM", 4),
        crawler_daily_budget=_int("CRAWLER_DAILY_BUDGET", 20),
        crawler_cache_ttl_seconds=_int("CRAWLER_CACHE_TTL_SECONDS", 7 * 86400),
        crawler_negative_ttl_seconds=_int("CRAWLER_NEGATIVE_TTL_SECONDS", 86400),
        mca_source_url=_str("MCA_SOURCE_URL", "https://api.data.gov.in/resource/4dbe5667-7b6b-41d7-82af-211562424d9a"),
        nse_source_url=_str("NSE_SOURCE_URL", "https://www.nseindia.com/regulations/member-sebi-debarred-entities"),
        db_path=_path("OSINT_DB_PATH", BACKEND_DIR / "data" / "osint.db"),
        raw_dir=_path("OSINT_RAW_DIR", BACKEND_DIR / "data" / "raw"),
        user_agent=_str("HTTP_USER_AGENT", "osint-fin-intel/0.1 (research project)"),
        http_timeout=_float("HTTP_TIMEOUT", 30.0),
        http_max_retries=_int("HTTP_MAX_RETRIES", 3),
        gleif_rpm=_int("GLEIF_RPM", 50),
        ai_provider=_str("AI_PROVIDER", "codex").lower(),
        ai_model=_str("AI_MODEL", "gpt-5.6-terra"),
        ai_rpm_limit=_int("AI_RPM_LIMIT", 2),
        ai_rpm_safety_margin_seconds=_float("AI_RPM_SAFETY_MARGIN_SECONDS", 5.0),
        ai_max_concurrent_requests=_int("AI_MAX_CONCURRENT_REQUESTS", 1),
        ai_max_retries=_int("AI_MAX_RETRIES", 1),
        ai_request_timeout=_float("AI_REQUEST_TIMEOUT", 60.0),
        ai_cache_ttl_seconds=_int("AI_CACHE_TTL_SECONDS", 3600),
        ai_daily_request_budget=_int("AI_DAILY_REQUEST_BUDGET", 40),
        ai_daily_token_budget=_int("AI_DAILY_TOKEN_BUDGET", 0),
        ai_queue_max=_int("AI_QUEUE_MAX", 8),
        ai_queue_max_wait_seconds=_float("AI_QUEUE_MAX_WAIT_SECONDS", 75.0),
        ai_background_reserve=_int("AI_BACKGROUND_RESERVE", 10),
        ai_429_cooldown_seconds=_float("AI_429_COOLDOWN_SECONDS", 120.0),
        codex_token_file=_path("CODEX_TOKEN_FILE", BACKEND_DIR.parent / "auth_session.json"),
        codex_responses_url=_str("CODEX_RESPONSES_URL", "https://chatgpt.com/backend-api/codex/responses"),
        codex_reasoning_effort=_str("CODEX_REASONING_EFFORT", "low"),
        codex_account_id=_str("CODEX_ACCOUNT_ID", ""),
        codex_originator=_str("CODEX_ORIGINATOR", "custom_python_script"),
        codex_instructions_file=_optional_path("CODEX_INSTRUCTIONS_FILE"),
        db_backend=_str("DB_BACKEND", "motherduck" if os.environ.get("MOTHERDUCK_TOKEN") else "sqlite").lower(),
        motherduck_token=_str("MOTHERDUCK_TOKEN", ""),
        motherduck_database=_str("MOTHERDUCK_DATABASE", "planc"),
        duckdb_path=_path("DUCKDB_PATH", BACKEND_DIR / "data" / "osint.duckdb"),
        cors_origins=_str("CORS_ORIGINS", "http://localhost:3000,https://*.vercel.app"),
        gcp_project_id=_str("GCP_PROJECT_ID", os.environ.get("GOOGLE_CLOUD_PROJECT", "")),
        bigquery_dataset=_str("BIGQUERY_DATASET", "planc"),
        bigquery_location=_str("BIGQUERY_LOCATION", "US"),
        gcs_bucket=_str("GCS_BUCKET", ""),
    )


settings = load_settings()
