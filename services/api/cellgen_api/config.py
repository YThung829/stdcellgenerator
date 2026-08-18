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
    e2b_template: str = os.environ.get("CELLGEN_E2B_TEMPLATE", "cellgen-engine")
    cors_origins: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
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
