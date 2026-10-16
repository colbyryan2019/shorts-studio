"""Running ffmpeg, with errors the user can act on."""

from __future__ import annotations

import shutil
import subprocess

from .errors import PipelineError

FPS = 30
WIDTH, HEIGHT = 1080, 1920


def frames(seconds: float) -> int:
    """Whole frames for a duration; every step uses this so video and audio stay in sync."""
    return max(1, round(seconds * FPS))


def run_ffmpeg(args: list[str], what: str) -> None:
    if not shutil.which("ffmpeg"):
        raise PipelineError("ffmpeg not found. Install it (e.g. `brew install ffmpeg`) so it is on PATH.")
    result = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], capture_output=True, text=True)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        raise PipelineError(f"ffmpeg failed while {what}:\n{tail}")
