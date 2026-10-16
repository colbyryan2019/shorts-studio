"""Step 3: narration. One text-to-speech request per shot line, so each line's duration is known exactly.

Each line is saved as audio/NN.mp3 and recorded in audio/manifest.json with a fingerprint of what it
was made from (line text, voice, model) and its measured duration. A re-run only re-reads lines whose
fingerprint changed, plus any --redo-voice N. Later steps read shot durations from the manifest.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .config import load_settings
from .episode import Episode
from .errors import PipelineError
from .schema import Script, Shot
from .series import Series

MANIFEST = "manifest.json"
FAKE_MODEL = "fake"
OUTPUT_FORMAT = "mp3_44100_128"
PAUSE_AFTER_LINE = 0.3  # seconds of silence after each line
MIN_SHOT_SECONDS = 1.5
FAKE_WORDS_PER_SECOND = 2.7  # placeholder audio length, close to a steady narrator


@dataclass
class LinePlan:
    shot: Shot
    path: Path
    previous_text: str  # neighbouring lines, so the delivery flows from line to line
    next_text: str
    fingerprint: str
    reason: str  # why it will be read; "" means the existing audio is reused


class VoiceClient(Protocol):
    model: str
    voice_id: str

    def speak(self, text: str, previous_text: str, next_text: str) -> bytes:
        """MP3 bytes of `text` read in the series voice."""
        ...


def audio_dir(ep: Episode) -> Path:
    return ep.path / "audio"


def shot_seconds(line_seconds: float) -> float:
    return round(max(line_seconds + PAUSE_AFTER_LINE, MIN_SHOT_SECONDS), 3)


def _fingerprint(text: str, voice_id: str, model: str) -> str:
    return hashlib.sha256(json.dumps({"text": text, "voice": voice_id, "model": model}).encode()).hexdigest()


def _read_manifest(ep: Episode) -> dict:
    path = audio_dir(ep) / MANIFEST
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def plan_voice(ep: Episode, script: Script, voice_id: str, model: str, redo: set[int]) -> list[LinePlan]:
    unknown = sorted(n for n in redo if n not in {s.n for s in script.shots})
    if unknown:
        raise PipelineError(f"--redo-voice: no shot {', '.join(map(str, unknown))} in this script")
    manifest = _read_manifest(ep)
    shots = script.shots
    plans = []
    for i, shot in enumerate(shots):
        path = audio_dir(ep) / f"{shot.n:02d}.mp3"
        fp = _fingerprint(shot.narration, voice_id, model)
        if shot.n in redo:
            reason = "--redo-voice"
        elif not path.exists():
            reason = "new"
        elif manifest.get(path.name, {}).get("fingerprint") != fp:
            reason = "changed since last read"
        else:
            reason = ""
        plans.append(LinePlan(
            shot, path,
            previous_text=shots[i - 1].narration if i > 0 else "",
            next_text=shots[i + 1].narration if i + 1 < len(shots) else "",
            fingerprint=fp, reason=reason,
        ))
    return plans


def describe_voice_plan(plans: list[LinePlan]) -> str:
    todo = [p for p in plans if p.reason]
    lines = [f"Narration: {len(todo)} lines to read, {len(plans) - len(todo)} reused"]
    for p in plans:
        lines.append(f"  {p.path.name}  {p.reason or 'reuse'}  \"{p.shot.narration}\"")
    if todo:
        chars = sum(len(p.shot.narration) for p in todo)
        lines.append(f"ElevenLabs usage: about {chars} characters (counted against your plan's credits)")
    return "\n".join(lines)


def make_voice(client: VoiceClient, ep: Episode, plans: list[LinePlan],
               probe=None) -> list[tuple[int, float]]:
    """Read the planned lines. Returns (shot number, shot duration) for every shot."""
    probe = probe or audio_seconds
    out = audio_dir(ep)
    out.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(ep)
    for p in plans:
        if not p.reason:
            continue
        audio = client.speak(p.shot.narration, p.previous_text, p.next_text)
        if not audio:
            raise PipelineError(f"The voice API returned no audio for shot {p.shot.n}.")
        p.path.write_bytes(audio)
        seconds = probe(p.path)
        manifest[p.path.name] = {
            "fingerprint": p.fingerprint,
            "text": p.shot.narration,
            "voice_id": client.voice_id,
            "model": client.model,
            "characters": len(p.shot.narration),
            "line_seconds": round(seconds, 3),
            "shot_seconds": shot_seconds(seconds),
        }
        # Saved after every line, so an interrupted run keeps (and won't re-pay for) what it read.
        (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"  {p.path.name}  ({p.reason})  {seconds:.2f}s")
    return [(p.shot.n, manifest[p.path.name]["shot_seconds"]) for p in plans]


def shot_durations(ep: Episode, script: Script) -> dict[int, float]:
    """Shot number -> seconds, for the motion and assembly steps. Fails if any line is missing or stale."""
    manifest = _read_manifest(ep)
    durations = {}
    for shot in script.shots:
        entry = manifest.get(f"{shot.n:02d}.mp3")
        if not entry or entry.get("text") != shot.narration or not (audio_dir(ep) / f"{shot.n:02d}.mp3").exists():
            raise PipelineError(f"Narration for shot {shot.n} is missing or out of date. Run the voice step first.")
        durations[shot.n] = entry["shot_seconds"]
    return durations


def audio_seconds(path: Path) -> float:
    """Exact length of an audio file, measured with ffprobe."""
    if not shutil.which("ffprobe"):
        raise PipelineError("ffprobe not found. Install ffmpeg (e.g. `brew install ffmpeg`) so it is on PATH.")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError as e:
        raise PipelineError(f"Could not measure the length of {path}: {result.stderr.strip() or 'no duration'}") from e


class ElevenLabsVoiceClient:
    def __init__(self, api_key: str, voice_id: str, model: str):
        from elevenlabs.client import ElevenLabs

        # Key passed explicitly, so the SDK never falls back to the shell environment.
        self._client = ElevenLabs(api_key=api_key, timeout=120)
        self.voice_id = voice_id
        self.model = model

    def speak(self, text: str, previous_text: str, next_text: str) -> bytes:
        from elevenlabs.core.api_error import ApiError

        try:
            chunks = self._client.text_to_speech.convert(
                self.voice_id, text=text, model_id=self.model, output_format=OUTPUT_FORMAT,
                previous_text=previous_text or None, next_text=next_text or None,
                request_options={"max_retries": 4},  # backoff on rate limits and server errors
            )
            return b"".join(chunks)
        except ApiError as e:
            raise PipelineError(_explain(e, self.voice_id, self.model)) from e
        except Exception as e:  # network errors come from httpx
            if type(e).__module__.startswith("httpx"):
                raise PipelineError("Could not reach the ElevenLabs API. Check your internet connection.") from e
            raise


def _explain(e, voice_id: str, model: str) -> str:
    status = getattr(e, "status_code", None)
    body = getattr(e, "body", None)
    detail = body.get("detail") if isinstance(body, dict) else body
    message = detail.get("message", "") if isinstance(detail, dict) else str(detail or "")
    if status == 401:
        return f"ElevenLabs rejected the request (401). Check ELEVENLABS_API_KEY in .env, or your credits. {message}".strip()
    if status == 404:
        return f"ElevenLabs couldn't find voice '{voice_id}' or model '{model}'. Check voice_id in series.yaml and VOICE_MODEL in .env."
    return f"ElevenLabs API error {status} (after retries): {message}".strip()


@dataclass
class FakeVoiceClient:
    """Makes silent MP3s about as long as the line would take to read. Records each call for tests."""

    model: str = FAKE_MODEL
    voice_id: str = "fake"
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    def speak(self, text: str, previous_text: str, next_text: str) -> bytes:
        self.calls.append((text, previous_text, next_text))
        if not shutil.which("ffmpeg"):
            raise PipelineError("ffmpeg not found. Install it (e.g. `brew install ffmpeg`) so it is on PATH.")
        seconds = max(len(text.split()) / FAKE_WORDS_PER_SECOND, 0.5)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "line.mp3"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                 "-t", f"{seconds:.3f}", "-c:a", "libmp3lame", "-b:a", "64k", "-y", str(out)],
                check=True,
            )
            return out.read_bytes()


def voice_setup_problem(series: Series, root: Path) -> str | None:
    """Why the real voice step can't run yet, or None if it can."""
    settings = load_settings(root)
    missing = []
    if not settings.elevenlabs_api_key:
        missing.append(f"ELEVENLABS_API_KEY in {root / '.env'}")
    if not settings.voice_model:
        missing.append(f"VOICE_MODEL in {root / '.env'} (e.g. eleven_multilingual_v2)")
    if not series.config.voice_id:
        missing.append(f"voice_id in {series.path / 'series.yaml'}")
    return ("set " + ", ".join(missing)) if missing else None


def make_voice_client(fake: bool, series: Series, root: Path) -> VoiceClient:
    if fake:
        return FakeVoiceClient()
    problem = voice_setup_problem(series, root)
    if problem:
        raise PipelineError(f"Narration needs setup: {problem} (or run with --fake-voice to test without it).")
    settings = load_settings(root)
    return ElevenLabsVoiceClient(settings.elevenlabs_api_key, series.config.voice_id, settings.voice_model)
