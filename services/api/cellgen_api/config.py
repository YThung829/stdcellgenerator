"""Runtime configuration, all overridable by environment variable."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class Settings:
    backend: str = os.environ.get("CELLGEN_BACKEND", "local")
    engine_src: Path = Path(os.environ.get("CELLGEN_ENGINE_SRC", REPO_ROOT / "engine"))
    data_root: Path = Path(os.environ.get("CELLGEN_DATA_ROOT", REPO_ROOT / ".cellgen"))
    opencode_bin: str = (
        os.environ.get("CELLGEN_OPENCODE_BIN") or shutil.which("opencode") or "opencode"
    )
    mongo_url: str | None = os.environ.get("CELLGEN_MONGO_URL") or None
    e2b_api_key: str | None = os.environ.get("E2B_API_KEY") or None
    # Names forwarded from this process into every sandbox. The agent inside
    # needs a model provider to be useful at all, and on E2B nothing is
    # inherited -- the sandbox is a different machine. An allowlist rather than
    # the whole environment: these are secrets, and handing a sandbox
    # everything this process happens to hold is not something to do by
    # accident. Extend with CELLGEN_SANDBOX_ENV=NAME1,NAME2.
    sandbox_env_names: list[str] = None  # type: ignore[assignment]
    e2b_template: str = os.environ.get("CELLGEN_E2B_TEMPLATE", "cellgen-engine")
    # Pause a workspace nobody has touched for this long. Idle sandboxes are
    # not free -- on E2B they bill for as long as they run -- and pausing is
    # lossless: it snapshots first, and resuming restores the conversation.
    # Set to 0 to leave sandboxes running.
    idle_pause_seconds: float = float(
        os.environ.get("CELLGEN_IDLE_PAUSE_SECONDS", 30 * 60))
    reaper_interval_seconds: float = float(
        os.environ.get("CELLGEN_REAPER_INTERVAL_SECONDS", 60))
    cors_origins: list[str] = None  # type: ignore[assignment]

    DEFAULT_SANDBOX_ENV = (
        "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY", "OPENAI_BASE_URL",
        "OPENROUTER_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
        "GROQ_API_KEY", "MISTRAL_API_KEY", "XAI_API_KEY",
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION",
        "OPENCODE_CONFIG_CONTENT",
    )

    def sandbox_env(self) -> dict[str, str]:
        """The provider credentials to hand a sandbox, as name -> value.

        Only names that are actually set here are forwarded, so an unset
        provider simply does not appear rather than arriving as an empty
        string that opencode would treat as configured.
        """
        return {name: os.environ[name]
                for name in self.sandbox_env_names
                if os.environ.get(name)}

    def __post_init__(self):
        if self.sandbox_env_names is None:
            extra = os.environ.get("CELLGEN_SANDBOX_ENV", "")
            self.sandbox_env_names = list(self.DEFAULT_SANDBOX_ENV) + [
                n.strip() for n in extra.split(",") if n.strip()
            ]
        if self.cors_origins is None:
            raw = os.environ.get("CELLGEN_CORS_ORIGINS", "http://localhost:5173")
            self.cors_origins = [o.strip() for o in raw.split(",") if o.strip()]
        self.data_root = Path(self.data_root)
        self.engine_src = Path(self.engine_src)

    @property
    def sandbox_root(self) -> Path:
        return self.data_root / "sandboxes"

    @property
    def store_root(self) -> Path:
        return self.data_root / "store"


settings = Settings()
