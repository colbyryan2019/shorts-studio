"""Step 2: one 1080x1920 still per shot, in the series style, guided by the reference images.

Each image is recorded in images/manifest.json with a fingerprint of everything it was made from
(style line, image prompt, reference images, model, quality, size). On a re-run only shots whose
fingerprint changed are redrawn, so editing one shot's image_prompt costs one image, not eleven.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .episode import Episode
from .errors import PipelineError
from .imagegen import ROUGH_COST_PER_IMAGE, ImageClient, fit_to_frame
from .schema import Script, Shot
from .series import Series

MANIFEST = "manifest.json"


@dataclass
class ShotPlan:
    shot: Shot
    path: Path
    prompt: str
    refs: list[Path]
    fingerprint: str
    reason: str  # why it will be drawn; "" means the existing image is reused


def images_dir(ep: Episode) -> Path:
    return ep.path / "images"


def shot_prompt(series: Series, shot: Shot) -> str:
    parts = [series.style_line, "", shot.image_prompt]
    if shot.refs:
        parts += [
            "",
            "Draw these exactly as they look in the reference images, in the same order: "
            + "; ".join(f"image {i}: the {name}" for i, name in enumerate(shot.refs, start=1))
            + ". Keep their shapes, proportions and details identical; only pose and placement change.",
        ]
    parts += ["", "Vertical 9:16 frame. No text, captions or borders."]
    return "\n".join(parts)


def _fingerprint(prompt: str, refs: list[Path], model: str, quality: str, size: str) -> str:
    h = hashlib.sha256()
    h.update(json.dumps({"prompt": prompt, "model": model, "quality": quality, "size": size}).encode())
    for p in refs:
        h.update(p.name.encode())
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def _read_manifest(ep: Episode) -> dict:
    path = images_dir(ep) / MANIFEST
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def plan_images(series: Series, ep: Episode, script: Script, model: str, redo: set[int]) -> list[ShotPlan]:
    unknown = sorted(n for n in redo if n not in {s.n for s in script.shots})
    if unknown:
        raise PipelineError(f"--redo: no shot {', '.join(map(str, unknown))} in this script")
    manifest = _read_manifest(ep)
    plans = []
    for shot in script.shots:
        refs = []
        for name in shot.refs:
            ref = series.ref_image(name)
            if ref is None:
                raise PipelineError(f"shot {shot.n} uses ref '{name}' but refs/ has no image for it")
            refs.append(ref)
        prompt = shot_prompt(series, shot)
        fp = _fingerprint(prompt, refs, model, series.config.image_quality, series.config.image_size)
        path = images_dir(ep) / f"{shot.n:02d}.png"
        recorded = manifest.get(path.name, {}).get("fingerprint")
        if shot.n in redo:
            reason = "--redo"
        elif not path.exists():
            reason = "new"
        elif recorded != fp:
            reason = "changed since last drawn"
        else:
            reason = ""
        plans.append(ShotPlan(shot, path, prompt, refs, fp, reason))
    return plans


def describe_plan(plans: list[ShotPlan], quality: str) -> str:
    todo = [p for p in plans if p.reason]
    lines = [f"Images: {len(todo)} to draw, {len(plans) - len(todo)} reused"]
    for p in plans:
        refs = f" refs: {', '.join(r.stem for r in p.refs)}" if p.refs else ""
        lines.append(f"  {p.path.name}  {p.reason or 'reuse'}{refs}")
    rough = ROUGH_COST_PER_IMAGE.get(quality)
    if todo and rough is not None:
        lines.append(f"Rough cost: about ${rough * len(todo):.2f} ({len(todo)} x ~${rough:.2f} at {quality} quality)")
    return "\n".join(lines)


def make_images(client: ImageClient, series: Series, ep: Episode, plans: list[ShotPlan]) -> float | None:
    """Draw the planned images. Returns the total cost when the model's rates are known."""
    out = images_dir(ep)
    out.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(ep)
    total, known = 0.0, True
    for p in plans:
        if not p.reason:
            continue
        image = client.generate(p.prompt, series.config.image_size, p.refs)
        fit_to_frame(image.png).save(p.path, format="PNG")
        cost = image.cost(client.model)
        manifest[p.path.name] = {
            "fingerprint": p.fingerprint,
            "model": client.model,
            "prompt": p.prompt,
            "refs": [r.name for r in p.refs],
            "tokens": {"image_in": image.input_image_tokens, "text_in": image.input_text_tokens,
                       "out": image.output_tokens},
            "cost_usd": cost,
        }
        # Saved after every image, so an interrupted run keeps (and won't re-pay for) what it drew.
        (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if cost is None:
            known = False
        else:
            total += cost
        price = f"  ${cost:.3f}" if cost is not None else ""
        print(f"  {p.path.name}  ({p.reason}){price}")
    return total if known else None
