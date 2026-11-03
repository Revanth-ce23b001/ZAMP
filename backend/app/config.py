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


KNOWN_PROVIDERS = ("gemini", "anthropic")


@dataclass(frozen=True)
class ChainEntry:
    """One provider + model in an extraction fallback chain (DEC-013)."""
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


def parse_chain(spec: str | None) -> tuple[ChainEntry, ...]:
    """'gemini:gemini-3.6-flash, gemini:gemini-3.5-flash-lite' -> entries, in order."""
    entries = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        provider, sep, model = part.partition(":")
        if not sep or not provider.strip() or not model.strip():
            raise ConfigError(f"extraction chain entry {part!r} must be provider:model")
        entries.append(ChainEntry(provider.strip().lower(), model.strip()))
    return tuple(entries)


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
    # Ordered fallback chains (DEC-013). Default: the single LLM_PROVIDER:LLM_MODEL entry.
    text_chain: tuple[ChainEntry, ...] = ()
    vision_chain: tuple[ChainEntry, ...] = ()
    # Shipped cache snapshot loaded when extraction_cache is empty (DEC-014). None disables.
    cache_snapshot: Path | None = BACKEND_DIR / "seed" / "extraction_cache.json"

    def secrets(self) -> list[str]:
        """Secret values that must never appear in responses, logs or audit payloads."""
        return [s for s in (self.demo_reset_token, self.anthropic_api_key, self.gemini_api_key) if s]

    def api_key_for(self, provider: str) -> str | None:
        return {"gemini": self.gemini_api_key, "anthropic": self.anthropic_api_key}.get(provider)

    def validate_chains(self) -> None:
        """Startup check: both chains non-empty, known providers, credentials present."""
        for name, chain in (("text", self.text_chain), ("vision", self.vision_chain)):
            if not chain:
                raise ConfigError(
                    f"the {name} extraction chain is empty: set EXTRACTION_{name.upper()}_CHAIN "
                    "or LLM_PROVIDER and LLM_MODEL")
            for entry in chain:
                if entry.provider not in KNOWN_PROVIDERS:
                    raise ConfigError(f"unknown provider {entry.provider!r} in the {name} chain")
                if not self.api_key_for(entry.provider):
                    raise ConfigError(f"{entry} is in the {name} chain but its API key is not set")


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
    provider, model = _env("LLM_PROVIDER"), _env("LLM_MODEL")
    single = (ChainEntry(provider.lower(), model),) if provider and model else ()
    text_chain = parse_chain(_env("EXTRACTION_TEXT_CHAIN")) or single
    vision_chain = parse_chain(_env("EXTRACTION_VISION_CHAIN")) or single
    snapshot = _env("CACHE_SNAPSHOT")
    return Settings(
        text_chain=text_chain,
        vision_chain=vision_chain,
        cache_snapshot=(None if (snapshot or "").lower() == "off"
                        else Path(snapshot) if snapshot else BACKEND_DIR / "seed" / "extraction_cache.json"),
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
