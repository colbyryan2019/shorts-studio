"""Running ffmpeg, with errors the user can act on."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

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


SILENT_LUFS = -60.0  # quieter than this counts as silence (e.g. placeholder narration)


def loudness(paths: list[Path], start: float = 0.0, seconds: float | None = None) -> float | None:
    """Integrated loudness (LUFS) of the files played back to back, or None if silent.

    `start` and `seconds` limit the measurement to the part of a single file that will be heard.
    """
    if not shutil.which("ffmpeg"):
        raise PipelineError("ffmpeg not found. Install it (e.g. `brew install ffmpeg`) so it is on PATH.")
    args = ["ffmpeg", "-hide_banner", "-nostats"]
    for path in paths:
        if start:
            args += ["-ss", f"{start:.3f}"]
        if seconds:
            args += ["-t", f"{seconds:.3f}"]
        args += ["-i", str(path)]
    joined = "".join(f"[{i}:a]" for i in range(len(paths))) + f"concat=n={len(paths)}:v=0:a=1,"
    args += ["-filter_complex", joined + "ebur128", "-f", "null", "-"]
    result = subprocess.run(args, capture_output=True, text=True)
    found = re.findall(r"I:\s+(-?[\d.]+|-inf) LUFS", result.stderr)
    if result.returncode != 0 or not found:
        raise PipelineError(f"Could not measure the loudness of {paths[0].name}: "
                            + "\n".join(result.stderr.strip().splitlines()[-3:]))
    value = float(found[-1]) if found[-1] != "-inf" else float("-inf")
    return value if value > SILENT_LUFS else None
