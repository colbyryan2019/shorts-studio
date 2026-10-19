"""Step 6: review.md, a sheet for checking an episode before it is posted (by a human; nothing is posted).

It lists the title, the full narration, every shot with its length and motion (planned and rendered),
the claims with their source files, any video fallbacks, a contact sheet of all stills and the cost
log summary. Rewritten on every run, so it always matches what is on disk.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .cost import format_step, read_costs, summarize
from .episode import Episode
from .images import images_dir
from .motion import MANIFEST as CLIPS_MANIFEST
from .motion import clips_dir
from .schema import Script
from .script import STATUS_LABELS
from .series import Series

REVIEW_FILE = "review.md"
CONTACT_SHEET = "contact_sheet.png"
THUMB = (216, 384)  # 1080x1920 at a fifth
COLUMNS = 5
GAP = 8
LABEL_HEIGHT = 40
BACKGROUND = (24, 24, 24)


@dataclass
class Review:
    path: Path
    contact_sheet: Path | None
    total_usd: float
    unpriced: int  # paid calls with no dollar amount (e.g. ElevenLabs characters)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except json.JSONDecodeError:
        return {}


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def contact_sheet(ep: Episode, script: Script, motions: dict[int, str], font: Path | None) -> Path | None:
    """All stills in a grid, each labelled with its shot number and motion. None if there are no stills."""
    images = {s.n: images_dir(ep) / f"{s.n:02d}.png" for s in script.shots}
    if not any(p.is_file() for p in images.values()):
        return None
    rows = -(-len(script.shots) // COLUMNS)
    cols = min(COLUMNS, len(script.shots))
    w, h = THUMB
    sheet = Image.new("RGB", (GAP + cols * (w + GAP), GAP + rows * (h + LABEL_HEIGHT + GAP)), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    label_font = ImageFont.truetype(str(font), 24) if font else ImageFont.load_default(size=24)
    for i, shot in enumerate(script.shots):
        x = GAP + (i % COLUMNS) * (w + GAP)
        y = GAP + (i // COLUMNS) * (h + LABEL_HEIGHT + GAP)
        if images[shot.n].is_file():
            with Image.open(images[shot.n]) as img:
                sheet.paste(img.convert("RGB").resize(THUMB, Image.Resampling.LANCZOS), (x, y))
        else:
            draw.rectangle((x, y, x + w - 1, y + h - 1), outline=(120, 120, 120), width=2)
            draw.text((x + w // 2, y + h // 2), "no image", fill=(160, 160, 160), font=label_font, anchor="mm")
        label = f"{shot.n} · {motions.get(shot.n, shot.motion)}"
        draw.text((x + 4, y + h + LABEL_HEIGHT // 2), label, fill=(235, 235, 235), font=label_font, anchor="lm")
    out = ep.path / CONTACT_SHEET
    sheet.save(out, format="PNG")
    return out


def write_review(series: Series, ep: Episode, script: Script, *, mode: str, status: str,
                 shot_seconds: dict[int, float] | None = None, font: Path | None = None) -> Review:
    clips = _read_json(clips_dir(ep) / CLIPS_MANIFEST)
    final = _read_json(ep.path / "final.json")
    rendered = {s.n: clips[f"{s.n:02d}.mp4"]["motion"] for s in script.shots
                if f"{s.n:02d}.mp4" in clips and "motion" in clips[f"{s.n:02d}.mp4"]}
    sheet = contact_sheet(ep, script, rendered, font)
    cfg = series.config

    facts = [f"{script.narration_words} words (target {cfg.words[0]}-{cfg.words[1]})", f"{len(script.shots)} shots"]
    if final.get("seconds"):
        facts.insert(0, f"{final['seconds']:.1f} s (target 60-70, aim for 65)")
    elif shot_seconds:
        facts.insert(0, f"about {sum(shot_seconds.values()):.1f} s of narration (target 60-70, aim for 65)")
    lines = [
        f"# {script.title}",
        "",
        f"{cfg.title} · episode {ep.number} · script {STATUS_LABELS[status]} · visual mode: {mode}",
        "",
        " · ".join(facts),
        "",
        f"End card: **{script.end_card}**",
        "",
    ]
    if (ep.path / "final.mp4").is_file():
        extras = [f"music: {final['music']}"] if final.get("music") else []
        extras += [f"ambience: {final['sfx']}"] if final.get("sfx") else []
        lines += [f"Video: [final.mp4](final.mp4)" + (f" ({', '.join(extras)})" if extras else ""), ""]
    else:
        lines += ["Video: not assembled yet.", ""]

    lines += [
        "## Before posting",
        "",
        "- [ ] Watch final.mp4 all the way through with sound on: narration, captions, music level, end card.",
        "- [ ] Check every claim below against its source file.",
        "- [ ] Look over the contact sheet: characters on model, one sun, no text or labels in the pictures.",
        "- [ ] It opens on a hook and ends on a cliffhanger, with no ask for comments or votes.",
        "",
        "## Narration",
        "",
        " ".join(s.narration for s in script.shots),
        "",
        "## Shots",
        "",
        "| # | Narration | Seconds | Motion |",
        "|---|---|---|---|",
    ]
    for shot in script.shots:
        seconds = f"{shot_seconds[shot.n]:.1f}" if shot_seconds and shot.n in shot_seconds else "-"
        motion = shot.motion
        if shot.n in rendered and rendered[shot.n] != shot.motion:
            motion = f"{shot.motion} → {rendered[shot.n]}"
        lines.append(f"| {shot.n} | {_cell(shot.narration)} | {seconds} | {motion} |")
    if not rendered:
        lines += ["", "Clips not rendered yet: the motion column shows the plan."]

    lines += ["", "## Claims", ""]
    if not script.claims:
        lines.append("None.")
    for i, claim in enumerate(script.claims, start=1):
        missing = "" if claim.source_file in series.sources else " **(not in sources/)**"
        lines.append(f"{i}. {claim.claim} (`sources/{claim.source_file}`{missing})")

    fallbacks = [(name, entry) for name, entry in sorted(clips.items()) if entry.get("fallback")]
    lines += ["", "## Video fallbacks", ""]
    if not fallbacks:
        lines.append("None.")
    for name, entry in fallbacks:
        lines.append(f"- {name}: used {entry.get('motion', '?')} instead: {_cell(str(entry['fallback']))}")

    lines += ["", "## Contact sheet", ""]
    lines.append(f"![Contact sheet]({CONTACT_SHEET})" if sheet else "No images yet.")

    steps = summarize(read_costs(ep))
    total = sum(s.usd for s in steps)
    unpriced = sum(s.unpriced for s in steps)
    lines += ["", "## Cost", ""]
    if not steps:
        lines.append("No paid calls logged for this episode (cost.json).")
    else:
        lines += [f"- {format_step(s)}" for s in steps]
        lines += ["", f"**Total: ${total:.2f}**" + (" plus ElevenLabs characters (plan credits)"
                                                     if any("characters" in s.units for s in steps) else "")]
    lines.append("")

    path = ep.path / REVIEW_FILE
    path.write_text("\n".join(lines), encoding="utf-8")
    return Review(path, sheet, total, unpriced)
