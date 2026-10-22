"""Load a series folder: series.yaml, bible, style line, sources, refs and recent episodes."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .errors import PipelineError

SOURCE_SUFFIXES = {".txt", ".md"}
REF_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
REF_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
RECENT_EPISODES = 3
EPISODE_DIR_RE = re.compile(r"^ep(\d+)$")
TODO_RE = re.compile(r"^\s*(?:[-*]\s*)?TODO:")  # placeholder lines left from templates/series/


class VoiceSettings(BaseModel):
    """Optional ElevenLabs delivery settings; anything left out uses the voice's own default."""

    model_config = ConfigDict(extra="forbid")

    stability: float | None = Field(default=None, ge=0, le=1)
    similarity_boost: float | None = Field(default=None, ge=0, le=1)
    style: float | None = Field(default=None, ge=0, le=1)
    speed: float | None = Field(default=None, ge=0.7, le=1.2)
    use_speaker_boost: bool | None = None

    def as_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


class SeriesConfig(BaseModel):
    """series.yaml. Unknown keys are rejected so typos don't silently do nothing."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    voice_id: str = ""
    words: tuple[int, int]
    shots: tuple[int, int]
    seconds: tuple[int, int] = (60, 70)  # target episode length; the review and final build flag anything outside
    max_genvideo_shots: int = Field(ge=0)
    end_card_format: str = ""
    end_card_cta: str = ""  # follow line under the end card, e.g. "Follow for part {next}"; {episode} works too
    default_motion: Literal["pushin", "rock", "drift"]
    hook_motion: Literal["", "punchin", "pushin", "rock", "drift"] = ""  # shot 1's motion; empty = as scripted
    caption_style: str = ""
    caption_font: str = ""  # a file in assets/fonts; empty = the first font found there
    music_volume_db: float = -20
    sfx_volume_db: float = -26  # ambience bed from sfx/; no files = no bed
    music_fade_in_seconds: float = Field(default=1.0, ge=0, le=5)  # music and ambience; 0 = full level at once
    line_pause_seconds: float = Field(default=0.3, ge=0, le=3)  # silence after each line
    playback_speed: float = Field(default=1.0, ge=0.5, le=2.0)  # whole final.mp4 sped up (or slowed), pitch kept
    voice_settings: VoiceSettings | None = None
    sources_required: bool = True
    research_max_searches: int = Field(default=5, ge=1, le=20)
    research_max_fetches: int = Field(default=3, ge=1, le=10)
    research_guidance: str = ""  # which sources to prefer; a generic default is used if empty
    image_quality: str = "medium"  # passed to the image model (e.g. low | medium | high)
    image_size: str = "1152x2048"  # requested size; 9:16 so it scales to 1080x1920 without bars
    ref_candidates: int = Field(default=3, ge=1, le=6)  # candidates per reference in make_refs.py

    @model_validator(mode="after")
    def _check_ranges(self) -> "SeriesConfig":
        for name in ("words", "shots", "seconds"):
            low, high = getattr(self, name)
            if low < 1 or high < low:
                raise ValueError(f"{name} must be [min, max] with 1 <= min <= max, got [{low}, {high}]")
        return self


class RefSpec(BaseModel):
    """One recurring character or object, from refs/refs.yaml."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    # Words in a shot's image_prompt that mean this ref is in the picture (whole words, any case).
    keywords: list[str] = Field(min_length=1)


@dataclass
class Series:
    name: str
    path: Path
    config: SeriesConfig
    house_rules: str
    bible: str
    style_line: str
    sources: dict[str, str]  # file name -> text
    refs: list[str]  # ref names (file stems), e.g. "dinghy" for refs/dinghy.png
    recent_scripts: dict[int, dict] = field(default_factory=dict)  # episode number -> script.json
    ref_specs: dict[str, RefSpec] = field(default_factory=dict)  # from refs/refs.yaml, may be empty

    @property
    def episodes_dir(self) -> Path:
        return self.path / "episodes"

    @property
    def refs_dir(self) -> Path:
        return self.path / "refs"

    def ref_image(self, name: str) -> Path | None:
        for suffix in sorted(REF_SUFFIXES):
            path = self.refs_dir / f"{name}{suffix}"
            if path.is_file():
                return path
        return None


def episode_dir_name(episode: int) -> str:
    return f"ep{episode:02d}"


def _read_required(path: Path, what: str) -> str:
    if not path.is_file():
        raise PipelineError(f"Missing {what}: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise PipelineError(f"{what} is empty: {path}")
    todo = [line.strip() for line in text.splitlines() if TODO_RE.match(line)]
    if todo:
        raise PipelineError(f"{what} still has {len(todo)} TODO line(s) from the template, starting with "
                            f"\"{todo[0][:80]}\". Fill them in (and delete the TODO lines): {path}")
    return text


def load_config(path: Path) -> SeriesConfig:
    if not path.is_file():
        raise PipelineError(f"Missing series config: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise PipelineError(f"{path} is not valid YAML:\n{e}") from e
    if not isinstance(raw, dict):
        raise PipelineError(f"{path} must be a mapping of settings")
    try:
        return SeriesConfig.model_validate(raw)
    except ValidationError as e:
        lines = [f"  - {'.'.join(str(p) for p in err['loc']) or '(top level)'}: {err['msg']}" for err in e.errors()]
        raise PipelineError(f"{path} has invalid settings:\n" + "\n".join(lines)) from e


def _load_sources(folder: Path) -> dict[str, str]:
    if not folder.is_dir():
        return {}
    sources = {}
    for f in sorted(folder.iterdir()):
        if f.is_file() and f.suffix.lower() in SOURCE_SUFFIXES and not f.name.startswith("."):
            text = f.read_text(encoding="utf-8").strip()
            if text:
                sources[f.name] = text
    return sources


def _load_refs(folder: Path) -> list[str]:
    if not folder.is_dir():
        return []
    return sorted(
        f.stem for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in REF_SUFFIXES and not f.name.startswith(".")
    )


def load_ref_specs(path: Path) -> dict[str, RefSpec]:
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise PipelineError(f"{path} is not valid YAML:\n{e}") from e
    if not isinstance(raw, dict):
        raise PipelineError(f"{path} must map each ref name to its description and keywords")
    specs = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not REF_NAME_RE.match(name):
            raise PipelineError(f"{path}: ref name '{name}' must be lowercase letters, digits and dashes")
        try:
            specs[name] = RefSpec.model_validate(value)
        except ValidationError as e:
            lines = [f"  - {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
            raise PipelineError(f"{path}: ref '{name}' is invalid:\n" + "\n".join(lines)) from e
    return specs


def _load_recent_scripts(episodes_dir: Path, before: int) -> dict[int, dict]:
    """script.json of the last RECENT_EPISODES episodes numbered below `before`."""
    if not episodes_dir.is_dir():
        return {}
    found = []
    for d in episodes_dir.iterdir():
        m = EPISODE_DIR_RE.match(d.name)
        if m and int(m.group(1)) < before and (d / "script.json").is_file():
            found.append((int(m.group(1)), d / "script.json"))
    recent = {}
    for number, path in sorted(found)[-RECENT_EPISODES:]:
        try:
            recent[number] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise PipelineError(f"Earlier episode script is not valid JSON: {path}\n{e}") from e
    return recent


def load_series(name: str, root: Path, episode: int) -> Series:
    series_root = root / "series"
    path = series_root / name
    if not path.is_dir():
        available = sorted(p.name for p in series_root.iterdir() if p.is_dir()) if series_root.is_dir() else []
        hint = f" Available: {', '.join(available)}" if available else ""
        raise PipelineError(f"Series '{name}' not found at {path}.{hint}")

    return Series(
        name=name,
        path=path,
        config=load_config(path / "series.yaml"),
        house_rules=_read_required(root / "house_rules.md", "house rules"),
        bible=_read_required(path / "bible.md", "series bible"),
        style_line=_read_required(path / "style_line.txt", "style line"),
        sources=_load_sources(path / "sources"),
        refs=_load_refs(path / "refs"),
        recent_scripts=_load_recent_scripts(path / "episodes", before=episode),
        ref_specs=load_ref_specs(path / "refs" / "refs.yaml"),
    )
