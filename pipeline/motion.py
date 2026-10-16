"""Step 4: turn each still into a clip of its shot's duration (1080x1920, 30 fps) with cheap ffmpeg motion.

`pushin`, `rock` and `drift` are built; `genvideo` shots use the series' default_motion until it is built.
Clips are recorded in clips/manifest.json with a fingerprint (image, duration, motion), so a
re-run only re-renders clips whose image or narration length changed, plus any --redo N.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .episode import Episode
from .errors import PipelineError
from .ffmpeg import FPS, HEIGHT, WIDTH, frames, run_ffmpeg
from .schema import Script, Shot

MANIFEST = "manifest.json"
BUILT_MOTIONS = {"pushin", "rock", "drift"}
MOTION_VERSION = 1  # bump when a motion's look changes, so existing clips are re-rendered
ZOOM = 0.10  # push-in goes from 100% to 110%
ROCK_SCALE = 1.15  # oversize so the tilted frame never shows its corners
ROCK_DEGREES = 2.0  # tilt either way
ROCK_PERIOD = 3.0  # seconds per full rock
ROCK_BOB = 12  # px of vertical bob, at output size
DRIFT_SCALE = 1.12  # how much wider than the frame the image is, i.e. how far it pans


@dataclass
class ClipPlan:
    shot: Shot
    image: Path
    path: Path
    seconds: float
    motion: str  # the motion actually rendered
    fingerprint: str
    reason: str  # why it will be rendered; "" means the existing clip is reused


def clips_dir(ep: Episode) -> Path:
    return ep.path / "clips"


def rendered_motion(motion: str, default_motion: str) -> str:
    return motion if motion in BUILT_MOTIONS else default_motion


def _read_manifest(ep: Episode) -> dict:
    path = clips_dir(ep) / MANIFEST
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def plan_clips(ep: Episode, script: Script, durations: dict[int, float], redo: set[int],
               default_motion: str) -> list[ClipPlan]:
    manifest = _read_manifest(ep)
    plans = []
    for shot in script.shots:
        image = ep.path / "images" / f"{shot.n:02d}.png"
        if not image.is_file():
            raise PipelineError(f"Shot {shot.n} has no image yet ({image}). Draw the images first.")
        motion = rendered_motion(shot.motion, default_motion)
        seconds = frames(durations[shot.n]) / FPS
        h = hashlib.sha256(hashlib.sha256(image.read_bytes()).digest())
        h.update(json.dumps({"frames": frames(seconds), "motion": motion, "v": MOTION_VERSION}).encode())
        fp = h.hexdigest()
        path = clips_dir(ep) / f"{shot.n:02d}.mp4"
        if shot.n in redo:
            reason = "--redo"
        elif not path.exists():
            reason = "new"
        elif manifest.get(path.name, {}).get("fingerprint") != fp:
            reason = "changed"
        else:
            reason = ""
        plans.append(ClipPlan(shot, image, path, seconds, motion, fp, reason))
    return plans


def make_clips(ep: Episode, plans: list[ClipPlan]) -> None:
    out = clips_dir(ep)
    out.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(ep)
    for p in plans:
        if not p.reason:
            continue
        RENDERERS[p.motion](p.image, p.path, frames(p.seconds), p.shot.n)
        manifest[p.path.name] = {"fingerprint": p.fingerprint, "motion": p.motion, "seconds": p.seconds}
        (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        note = "" if p.motion == p.shot.motion else f" ({p.shot.motion} not built yet)"
        print(f"  {p.path.name}  {p.motion}{note}  {p.seconds:.2f}s")


def render_pushin(image: Path, out: Path, n_frames: int, shot_n: int = 1) -> None:
    # Upscaling first keeps zoompan's whole-pixel steps small, so the zoom doesn't shimmer.
    step = ZOOM / max(n_frames - 1, 1)
    vf = (
        f"scale={WIDTH * 2}:{HEIGHT * 2},"
        f"zoompan=z='1+{step:.8f}*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={n_frames}:s={WIDTH}x{HEIGHT}:fps={FPS},format=yuv420p"
    )
    run_ffmpeg(
        ["-i", str(image), "-vf", vf, "-frames:v", str(n_frames), "-r", str(FPS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-an", str(out)],
        f"rendering {out.name}",
    )


# rock and drift work at 2x size and scale down at the end, so slow movement moves in half-pixel
# steps instead of visibly jumping a whole pixel at a time.

def render_rock(image: Path, out: Path, n_frames: int, shot_n: int = 1) -> None:
    """A boat on a gentle swell: a slow sine tilt plus a small bob, cropped so no corners show."""
    w, h = round(WIDTH * ROCK_SCALE) * 2, round(HEIGHT * ROCK_SCALE) * 2
    phase = (shot_n * 1.7) % 6.283  # each shot starts at a different point in the swell
    wave = f"sin(2*PI*t/{ROCK_PERIOD}+{phase:.3f})"
    bob = f"{ROCK_BOB * 2}*sin(2*PI*t/{ROCK_PERIOD}+{phase:.3f}+1.2)"
    vf = (
        f"scale={w}:{h},"
        f"rotate=a='{ROCK_DEGREES}*PI/180*{wave}':ow=iw:oh=ih:c=black,"
        f"crop={WIDTH * 2}:{HEIGHT * 2}:x='(iw-ow)/2':y='(ih-oh)/2+{bob}',"
        f"scale={WIDTH}:{HEIGHT},format=yuv420p"
    )
    _render_looped(image, out, n_frames, vf)


def render_drift(image: Path, out: Path, n_frames: int, shot_n: int = 1) -> None:
    """A slow, steady pan across a slightly oversized frame; alternate shots pan opposite ways."""
    w, h = round(WIDTH * DRIFT_SCALE) * 2, round(HEIGHT * DRIFT_SCALE) * 2
    progress = f"(n/{max(n_frames - 1, 1)})"
    x = f"(iw-ow)*{progress}" if shot_n % 2 else f"(iw-ow)*(1-{progress})"
    vf = (
        f"scale={w}:{h},"
        f"crop={WIDTH * 2}:{HEIGHT * 2}:x='{x}':y='(ih-oh)/2',"
        f"scale={WIDTH}:{HEIGHT},format=yuv420p"
    )
    _render_looped(image, out, n_frames, vf)


def _render_looped(image: Path, out: Path, n_frames: int, vf: str) -> None:
    run_ffmpeg(
        ["-loop", "1", "-framerate", str(FPS), "-i", str(image), "-vf", vf, "-frames:v", str(n_frames),
         "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-an", str(out)],
        f"rendering {out.name}",
    )


RENDERERS = {"pushin": render_pushin, "rock": render_rock, "drift": render_drift}
