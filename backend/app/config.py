"""Environment configuration (PRD §12.5, NFR-05, NFR-06, DEC-002).

Secrets come only from environment variables; none are stored in the repository.
The backend refuses to start without DEMO_RESET_TOKEN. LLM settings are optional
until extraction is built.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    database_path: Path
    uploads_dir: Path
    frontend_origin: str
    # repr=False keeps secrets out of logs and tracebacks.
    demo_reset_token: str | None = field(default=None, repr=False)
    llm_provider: str | None = None
    llm_model: str | None = None
    anthropic_api_key: str | None = field(default=None, repr=False)
    gemini_api_key: str | None = field(default=None, repr=False)
    samples_dir: Path = BACKEND_DIR / "samples"
    # FR-014 cache. EXTRACTION_CACHE=off disables both reads and writes (determinism testing).
    extraction_cache_enabled: bool = True

    def secrets(self) -> list[str]:
        """Secret values that must never appear in responses, logs or audit payloads."""
        return [s for s in (self.demo_reset_token, self.anthropic_api_key, self.gemini_api_key) if s]


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def load_settings(require_reset_token: bool = True) -> Settings:
    """Read settings from the environment.

    The API server always calls this with require_reset_token=True, so it cannot start
    without DEMO_RESET_TOKEN. CLI tools (migrate, seed) don't touch the reset endpoint
    and may skip the check.
    """
    token = _env("DEMO_RESET_TOKEN")
    if require_reset_token and token is None:
        raise ConfigError(
            "DEMO_RESET_TOKEN is not set. The backend refuses to start without it "
            "(PRD FR-116, DEC-002). Copy .env.example to .env and set a value."
        )
    return Settings(
        database_path=Path(_env("DATABASE_PATH") or BACKEND_DIR / "data" / "clearmatch.db"),
        uploads_dir=Path(_env("UPLOADS_DIR") or BACKEND_DIR / "storage" / "uploads"),
        frontend_origin=_env("FRONTEND_ORIGIN") or "http://localhost:3000",
        demo_reset_token=token,
        llm_provider=_env("LLM_PROVIDER"),
        llm_model=_env("LLM_MODEL"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        gemini_api_key=_env("GEMINI_API_KEY"),
        samples_dir=Path(_env("SAMPLES_DIR") or BACKEND_DIR / "samples"),
        extraction_cache_enabled=(_env("EXTRACTION_CACHE") or "on").lower() not in ("off", "0", "false", "no"),
    )
