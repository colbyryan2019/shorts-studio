"""Reference images: which recurring characters/objects a series needs, candidates for each, and the pick.

refs/refs.yaml     what each ref is and the words that mean it's in a shot (editable)
refs/candidates/   generated options, e.g. survivor-1.png, plus candidates.json recording how each was made
refs/<name>.png    the chosen one, used for every shot that shows it
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import PipelineError
from .imagegen import FAKE_MODEL, Budget, ImageClient
from .llm import LLMClient
from .script import extract_json
from .series import REF_NAME_RE, RefSpec, Series

REFS_FILE = "refs.yaml"
CANDIDATES_DIR = "candidates"
CANDIDATES_LOG = "candidates.json"
REF_IMAGE_SIZE = "1024x1024"


class _ProposedRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = Field(min_length=1)
    keywords: list[str] = Field(min_length=1)


class _Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refs: list[_ProposedRef] = Field(min_length=1)


def propose_system(series: Series) -> str:
    return "\n\n".join([
        f"You plan the reference images for \"{series.config.title}\", a series of short videos made of still "
        "images. Each reference image shows one recurring character or object exactly as it should look in "
        "every shot, so it stays consistent from shot to shot.",
        "# Series bible\n" + series.bible,
        "# Art style\n" + series.style_line,
        "# Output format\n"
        "Reply with one JSON object and nothing else:\n"
        '{"refs": [{"name": "lowercase-name", "description": "...", "keywords": ["...", "..."]}]}\n\n'
        "- Only things that recur across many episodes and must look the same each time (main characters, "
        "their vehicle or home, signature objects). Usually 1-4 refs. Not settings, weather or one-off props.\n"
        "- description: what it looks like, concretely (shape, clothing, colors, distinctive details), "
        "within the art style. Two or three sentences.\n"
        "- keywords: the words a shot description would use when this thing is in the picture, e.g. "
        '["survivor", "figure", "man"]. Single lowercase words or short phrases. Avoid words that would match '
        "shots where it isn't visible.",
    ])


def propose_refs(client: LLMClient, series: Series) -> dict[str, RefSpec]:
    reply = client.complete(propose_system(series), "List the reference images this series needs.")
    try:
        proposal = _Proposal.model_validate(extract_json(reply))
    except (ValueError, ValidationError) as e:
        raise PipelineError(f"Couldn't read the proposed refs from Claude's reply: {e}") from e
    specs = {}
    for ref in proposal.refs:
        name = re.sub(r"[^a-z0-9]+", "-", ref.name.strip().lower()).strip("-")  # "Signal Mirror" -> "signal-mirror"
        if not REF_NAME_RE.match(name):
            raise PipelineError(f"Claude proposed an invalid ref name '{ref.name}'. Run again or write refs.yaml by hand.")
        specs[name] = RefSpec(description=ref.description.strip(), keywords=[k.strip().lower() for k in ref.keywords])
    return specs


def save_ref_specs(series: Series, specs: dict[str, RefSpec]) -> Path:
    path = series.refs_dir / REFS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Recurring characters and objects. Edit freely: the descriptions drive the reference images,\n"
        "# and any keyword in a shot's image_prompt means that shot must list the ref.\n"
    )
    body = yaml.safe_dump({n: s.model_dump() for n, s in specs.items()}, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(header + body, encoding="utf-8")
    return path


def candidate_prompt(series: Series, name: str, spec: RefSpec, style: str | None = None) -> str:
    """style replaces the series style line, for trying out a different look."""
    return (
        f"{style or series.style_line}\n\n"
        f"Reference image of the {name}: {spec.description}\n"
        "Show only this one subject, whole and centered, in a neutral pose, on a plain empty background. "
        "No other objects, no scenery, no text."
    )


def _candidates_dir(series: Series) -> Path:
    return series.refs_dir / CANDIDATES_DIR


def _read_log(series: Series) -> dict:
    path = _candidates_dir(series) / CANDIDATES_LOG
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _write_log(series: Series, log: dict) -> None:
    path = _candidates_dir(series) / CANDIDATES_LOG
    path.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")


def candidate_path(series: Series, name: str, i: int) -> Path:
    return _candidates_dir(series) / f"{name}-{i}.png"


def _existing_numbers(series: Series, name: str) -> list[int]:
    nums = []
    for p in _candidates_dir(series).glob(f"{name}-*.png") if _candidates_dir(series).is_dir() else []:
        tail = p.stem[len(name) + 1:]
        if tail.isdigit():
            nums.append(int(tail))
    return sorted(nums)


def planned_candidates(
    series: Series, names: list[str], force: bool, refine: bool = False, count: int | None = None,
) -> list[tuple[str, int]]:
    """Which (name, number) to draw. Refining or trying a style always adds new numbers after the existing ones."""
    count = count or series.config.ref_candidates
    if refine:
        return [(n, max(_existing_numbers(series, n), default=0) + i) for n in names for i in range(1, count + 1)]
    return [(n, i) for n in names for i in range(1, count + 1) if force or not candidate_path(series, n, i).exists()]


def refine_prompt(series: Series, name: str, spec: RefSpec, style: str | None = None) -> str:
    return (
        candidate_prompt(series, name, spec, style)
        + "\nUse the attached image as the starting point: keep its pose, proportions and overall design, and "
        "redraw it to match the description and art style above. Where they differ from the image (for "
        "example a color or finish), follow the description."
    )


def generate_candidates(
    client: ImageClient, series: Series, names: list[str], budget: Budget, *,
    force: bool = False, base: int | None = None, style: str | None = None, count: int | None = None,
) -> list[Path]:
    """Make missing candidates (all, with force) within the budget. Returns the new files.

    With base=N (one name only), new candidates start from candidate N and get new numbers.
    """
    out_dir = _candidates_dir(series)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = _read_log(series)
    start: list[Path] = []
    if base is not None:
        if len(names) != 1:
            raise PipelineError("--from works with one ref at a time, e.g. --generate survivor --from 2")
        start = [candidate_path(series, names[0], base)]
        if not start[0].is_file():
            raise PipelineError(f"No candidate {start[0].name} to start from.")
    made = []
    new_numbers = base is not None or style is not None
    for name, i in planned_candidates(series, names, force, refine=new_numbers, count=count):
        spec = series.ref_specs[name]
        prompt = refine_prompt(series, name, spec, style) if start else candidate_prompt(series, name, spec, style)
        budget.check_next()
        image = client.generate(prompt, REF_IMAGE_SIZE, refs=start)
        path = candidate_path(series, name, i)
        path.write_bytes(image.png)
        log[path.name] = {"model": client.model, "prompt": prompt, "created": datetime.now(timezone.utc).isoformat(),
                          "from": start[0].name if start else None, "style": style}
        _write_log(series, log)  # after each image, so an interrupted run keeps what it paid for
        cost = image.cost(client.model)
        budget.add(cost)
        made.append(path)
        print(f"  {path.name}  ${cost or 0:.3f}")
    return made


def pick(series: Series, name: str, number: int) -> Path:
    if name not in series.ref_specs:
        known = ", ".join(series.ref_specs) or "none"
        raise PipelineError(f"'{name}' isn't in {REFS_FILE}. Known refs: {known}")
    src = candidate_path(series, name, number)
    if not src.is_file():
        raise PipelineError(f"No candidate {src.name}. Generate candidates first with --generate.")
    if _read_log(series).get(src.name, {}).get("model") == FAKE_MODEL:
        raise PipelineError(f"{src.name} is a --fake-images placeholder and can't be used as a real reference.")
    for old in series.refs_dir.glob(f"{name}.*"):
        if old.is_file() and old.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            old.unlink()
    dest = series.refs_dir / f"{name}.png"
    shutil.copy2(src, dest)
    return dest


def status_lines(series: Series) -> list[str]:
    lines = []
    for name, spec in series.ref_specs.items():
        image = series.ref_image(name)
        n = len(list(_candidates_dir(series).glob(f"{name}-*.png"))) if _candidates_dir(series).is_dir() else 0
        state = f"picked: {image.name}" if image else ("not picked yet" if n else "no candidates yet")
        lines.append(f"  {name}: {state} ({n} candidates)")
        lines.append(f"      {spec.description}")
        lines.append(f"      keywords: {', '.join(spec.keywords)}")
    return lines
