"""Step 1: write (or revise) an episode script with the LLM, validate it, summarise it."""

from __future__ import annotations

import json

from .errors import PipelineError
from .llm import LLMClient
from .schema import Script, ValidationResult, validate_script
from .series import Series

SCHEMA_EXAMPLE = {
    "title": "Short episode title",
    "end_card": "End card text",
    "shots": [
        {"n": 1, "narration": "One short spoken line.",
         "image_prompt": "What the still image shows, plainly described",
         "motion": "drift", "refs": ["name-of-ref"]},
    ],
    "claims": [{"claim": "A factual claim the narration makes", "source_file": "file-in-sources.txt"}],
}


def build_system(series: Series) -> str:
    cfg = series.config
    refs = ", ".join(series.refs) or "(none yet: every shot must use an empty refs list)"
    if cfg.sources_required:
        claims_rule = (
            "Every factual claim in the narration must come from the source material below and be listed in "
            "`claims` with the exact file name it came from. Do not state facts the sources don't support."
        )
    else:
        claims_rule = "List any factual claims in `claims`, citing a source file name where you have one."

    parts = [
        f"You write scripts for \"{cfg.title}\", a series of short vertical videos. "
        "Each script is narration split into shots, with a still image per shot.",
        "# House rules\nThese apply to every series. The series bible can add rules but never override these.\n\n"
        + series.house_rules,
        "# Series bible\n" + series.bible,
        "# Output format\n"
        "Reply with one JSON object and nothing else: no prose, no markdown code fences. Shape:\n"
        + json.dumps(SCHEMA_EXAMPLE, indent=2, ensure_ascii=False)
        + "\n\nRules for the JSON:\n"
        f"- {cfg.shots[0]}-{cfg.shots[1]} shots, numbered from 1 in order; one narration line per shot.\n"
        f"- Total narration across all shots: {cfg.words[0]}-{cfg.words[1]} words.\n"
        "- `motion` is one of: pushin, rock, drift, genvideo. "
        f"Use genvideo for at most {cfg.max_genvideo_shots} shots, only where real movement matters most.\n"
        f"- `refs` lists reference images to keep characters and objects consistent. Allowed names: {refs}.\n"
        f"- `end_card` follows this format: {cfg.end_card_format or '(free text)'}\n"
        "- `image_prompt` describes only the content of the image; the art style is added separately.\n"
        f"- {claims_rule}",
    ]
    if series.sources:
        parts.append(
            "# Source material\n"
            + "\n\n".join(f'<source file="{name}">\n{text}\n</source>' for name, text in series.sources.items())
        )
    return "\n\n".join(parts)


def build_task(series: Series, episode: int, outline: str, current: dict | None = None, notes: str | None = None) -> str:
    parts = []
    if series.recent_scripts:
        parts.append(
            "Previous episodes, for continuity (do not repeat their lessons or lines):\n"
            + "\n\n".join(
                f"Episode {n}:\n{json.dumps(s, indent=2, ensure_ascii=False)}" for n, s in series.recent_scripts.items()
            )
        )
    parts.append(f"Episode {episode} outline:\n{outline}")
    if current is None:
        parts.append(f"Write the script for episode {episode}.")
    else:
        parts.append(
            f"Here is the current script for episode {episode}:\n{json.dumps(current, indent=2, ensure_ascii=False)}"
        )
        parts.append(
            f"Revise it according to these notes:\n{notes}\n\n"
            "Change only what the notes ask for (plus anything needed to keep the rules). Return the full script."
        )
    return "\n\n".join(parts)


def build_fix_prompt(task: str, previous_reply: str, errors: list[str]) -> str:
    return (
        f"{task}\n\n"
        f"Your previous reply was:\n{previous_reply}\n\n"
        "It failed these checks:\n" + "\n".join(f"- {e}" for e in errors)
        + "\n\nReturn the full corrected script as one JSON object and nothing else."
    )


def extract_json(text: str) -> object:
    """Parse the reply, tolerating code fences or a stray sentence around the object."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("the reply contained no JSON object")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"the reply was not valid JSON: {e.msg} (line {e.lineno}, column {e.colno})") from e


def _attempt(client: LLMClient, system: str, prompt: str, series: Series, missing_sources_ok: bool):
    reply = client.complete(system, prompt)
    try:
        data = extract_json(reply)
    except ValueError as e:
        return reply, None, ValidationResult(script=None, errors=[str(e)])
    return reply, data, validate_script(data, series, missing_sources_ok=missing_sources_ok)


def write_script(
    client: LLMClient,
    series: Series,
    episode: int,
    outline: str,
    *,
    current: dict | None = None,
    notes: str | None = None,
    missing_sources_ok: bool = False,
) -> tuple[dict, ValidationResult]:
    """Ask the LLM for a script (new, or a revision of `current` per `notes`).

    On validation failure, retries once with the errors fed back, then raises PipelineError.
    Returns the raw JSON (as saved) and its validation result.
    """
    system = build_system(series)
    task = build_task(series, episode, outline, current=current, notes=notes)

    reply, data, result = _attempt(client, system, task, series, missing_sources_ok)
    if result.ok:
        return data, result

    first_errors = result.errors
    reply, data, result = _attempt(client, system, build_fix_prompt(task, reply, first_errors), series, missing_sources_ok)
    if result.ok:
        return data, result

    raise PipelineError(
        "The script failed validation twice.\nFirst attempt:\n"
        + "\n".join(f"  - {e}" for e in first_errors)
        + "\nSecond attempt:\n"
        + "\n".join(f"  - {e}" for e in result.errors)
    )


def format_summary(script: Script, series: Series, status: str, warnings: list[str], errors: list[str] = ()) -> str:
    cfg = series.config
    label = {"approved": "APPROVED", "draft": "DRAFT", "changed": "DRAFT (edited since approval)"}[status]
    lines = [
        f"{script.title}  [{label}]",
        f"End card: {script.end_card}",
        f"{script.narration_words} words (target {cfg.words[0]}-{cfg.words[1]}) · "
        f"{len(script.shots)} shots (target {cfg.shots[0]}-{cfg.shots[1]}) · "
        f"genvideo {script.genvideo_count}/{cfg.max_genvideo_shots}",
        "",
    ]
    for shot in script.shots:
        refs = f"  refs: {', '.join(shot.refs)}" if shot.refs else ""
        lines.append(f"{shot.n:>3}. [{shot.motion}] {shot.narration}")
        lines.append(f"       image: {shot.image_prompt}{refs}")
    lines.append("")
    lines.append("Claims:" if script.claims else "Claims: none")
    for claim in script.claims:
        lines.append(f"  - {claim.claim}  ({claim.source_file})")
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  ! {w}" for w in warnings)
    if errors:
        lines.append("")
        lines.append("Problems (fix before approving):")
        lines.extend(f"  x {e}" for e in errors)
    return "\n".join(lines)


def planned_calls(script: Script) -> str:
    n = len(script.shots)
    return "\n".join([
        "Planned paid calls for the remaining steps (none made in this run):",
        f"  images:  {n} (one per shot)",
        f"  voice:   {n} ElevenLabs lines",
        f"  video:   {script.genvideo_count} genvideo clips; the other {n - script.genvideo_count} shots use free ffmpeg motion",
    ])
