"""script.json schema (SPEC.md step 1) and validation against a series' limits."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .series import Series

Motion = Literal["pushin", "rock", "drift", "genvideo"]


class Shot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n: int
    narration: str = Field(min_length=1)
    image_prompt: str = Field(min_length=1)
    motion: Motion
    refs: list[str] = []


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1)
    source_file: str = Field(min_length=1)


class Script(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    end_card: str = Field(min_length=1)
    shots: list[Shot] = Field(min_length=1)
    claims: list[Claim] = []

    @property
    def narration_words(self) -> int:
        return sum(count_words(s.narration) for s in self.shots)

    @property
    def genvideo_count(self) -> int:
        return sum(1 for s in self.shots if s.motion == "genvideo")


def count_words(text: str) -> int:
    return len(text.split())


@dataclass
class ValidationResult:
    script: Script | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.script is not None and not self.errors


def validate_script(data: object, series: Series, *, missing_sources_ok: bool = False) -> ValidationResult:
    """Check structure, then the series limits. Errors are worded so they can be fed back to the LLM.

    missing_sources_ok turns "claim cites a file not in sources/" into a warning. It is only
    used in --fake-llm mode while sources/ is still empty.
    """
    try:
        script = Script.model_validate(data)
    except ValidationError as e:
        errors = [f"{_loc(err['loc'])}: {err['msg']}" for err in e.errors()]
        return ValidationResult(script=None, errors=errors)

    cfg = series.config
    errors: list[str] = []
    warnings: list[str] = []

    numbers = [s.n for s in script.shots]
    if numbers != list(range(1, len(numbers) + 1)):
        errors.append(f"shots must be numbered 1..{len(numbers)} in order, got {numbers}")

    lo, hi = cfg.shots
    if not lo <= len(script.shots) <= hi:
        errors.append(f"shot count is {len(script.shots)}, must be between {lo} and {hi}")

    lo, hi = cfg.words
    words = script.narration_words
    if not lo <= words <= hi:
        errors.append(f"narration is {words} words in total, must be between {lo} and {hi}")

    if script.genvideo_count > cfg.max_genvideo_shots:
        errors.append(
            f"{script.genvideo_count} shots use motion 'genvideo', at most {cfg.max_genvideo_shots} allowed"
        )

    known_refs = set(series.refs)
    for shot in script.shots:
        for ref in shot.refs:
            if ref not in known_refs:
                available = ", ".join(series.refs) or "none (refs/ is empty)"
                errors.append(f"shot {shot.n}: ref '{ref}' has no image in refs/. Available: {available}")

    for name, spec in series.ref_specs.items():
        if name not in known_refs:
            warnings.append(
                f"ref '{name}' has no picked image yet, so shots showing it may not look consistent. "
                "See make_refs.py."
            )
            continue
        for shot in script.shots:
            hit = matching_keyword(shot.image_prompt, spec.keywords)
            if hit and name not in shot.refs:
                errors.append(
                    f"shot {shot.n}: image_prompt mentions '{hit}', so the shot shows the {name}; "
                    f"add \"{name}\" to its refs"
                )

    if cfg.sources_required:
        known_sources = set(series.sources)
        for i, claim in enumerate(script.claims, start=1):
            if claim.source_file not in known_sources:
                available = ", ".join(sorted(known_sources)) or "none (sources/ is empty)"
                msg = f"claim {i}: source_file '{claim.source_file}' is not in sources/. Available: {available}"
                (warnings if missing_sources_ok else errors).append(msg)

    return ValidationResult(script=script, errors=errors, warnings=warnings)


def matching_keyword(text: str, keywords: list[str]) -> str | None:
    """The first keyword found in text as a whole word or phrase, ignoring case."""
    for kw in keywords:
        if kw.strip() and re.search(rf"(?<!\w){re.escape(kw.strip())}(?!\w)", text, re.IGNORECASE):
            return kw
    return None


def _loc(loc: tuple) -> str:
    # ("shots", 2, "motion") -> "shots[3].motion": counted from 1, like shot numbers and claim numbers
    out = ""
    for part in loc:
        out += f"[{part + 1}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "script"
