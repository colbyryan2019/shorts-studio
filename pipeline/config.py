"""Global settings, read only from the project's .env file."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from .errors import PipelineError

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    # repr=False so a key never ends up in a log line or traceback by accident.
    anthropic_api_key: str = field(default="", repr=False)
    llm_model: str = ""
    openai_api_key: str = field(default="", repr=False)
    image_provider: str = "openai"
    image_model: str = ""
    image_cost_limit: float = 2.00  # USD per run of the image steps


def load_settings(root: Path = PROJECT_ROOT) -> Settings:
    """Read settings from <root>/.env only.

    Deliberately ignores the shell environment: keys live in .env and nowhere else.
    """
    env_path = root / ".env"
    values = dotenv_values(env_path) if env_path.exists() else {}
    raw_limit = (values.get("IMAGE_COST_LIMIT") or "").strip()
    try:
        image_cost_limit = float(raw_limit) if raw_limit else Settings.image_cost_limit
    except ValueError as e:
        raise PipelineError(f"IMAGE_COST_LIMIT in .env must be a dollar amount like 2.00, got '{raw_limit}'") from e
    if image_cost_limit <= 0:
        raise PipelineError("IMAGE_COST_LIMIT in .env must be more than 0")
    return Settings(
        anthropic_api_key=(values.get("ANTHROPIC_API_KEY") or "").strip(),
        llm_model=(values.get("LLM_MODEL") or "").strip(),
        openai_api_key=(values.get("OPENAI_API_KEY") or "").strip(),
        image_provider=(values.get("IMAGE_PROVIDER") or "openai").strip().lower(),
        image_model=(values.get("IMAGE_MODEL") or "").strip(),
        image_cost_limit=image_cost_limit,
    )
