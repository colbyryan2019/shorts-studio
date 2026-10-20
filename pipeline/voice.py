"""Step 3: narration. One text-to-speech request per shot line, so each line's duration is known exactly.

Each line is saved as audio/NN.mp3 and recorded in audio/manifest.json with a fingerprint of what it
was made from (line text, voice, model, voice settings), its measured duration and the time each word
is spoken (for word-by-word captions). A re-run only re-reads lines whose fingerprint changed or that
have no word timings yet, plus any --redo-voice N. Later steps read shot durations from the manifest.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .config import load_settings
from .cost import log_cost
from .episode import Episode
from .errors import PipelineError
from .reuse import find_moved
from .schema import Script, Shot
from .series import Series

MANIFEST = "manifest.json"
FAKE_MODEL = "fake"
OUTPUT_FORMAT = "mp3_44100_128"
PAUSE_AFTER_LINE = 0.3  # default seconds of silence after each line (series.yaml line_pause_seconds)
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
    copy_from: str = ""  # an identical reading under another shot number, copied instead of read


Word = tuple[str, float, float]  # (word, start, end) in seconds from the start of the line


@dataclass
class Speech:
    audio: bytes  # MP3
    words: list[Word] | None  # None if the provider gave no timings


class VoiceClient(Protocol):
    model: str
    voice_id: str

    def speak(self, text: str, previous_text: str, next_text: str) -> Speech:
        """`text` read in the series voice, with the time each word is spoken."""
        ...


def audio_dir(ep: Episode) -> Path:
    return ep.path / "audio"


def shot_seconds(line_seconds: float, pause: float = PAUSE_AFTER_LINE) -> float:
    return round(max(line_seconds + pause, MIN_SHOT_SECONDS), 3)


def _pause(shot: Shot, default: float) -> float:
    """A shot's own pause_after (a held dramatic beat) wins over the series default."""
    return default if shot.pause_after is None else shot.pause_after


def _fingerprint(text: str, voice_id: str, model: str, settings: dict | None) -> str:
    inputs = {"text": text, "voice": voice_id, "model": model}
    if settings:  # only when set, so lines read before voice_settings existed stay valid
        inputs["settings"] = settings
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def words_from_alignment(characters: list[str], starts: list[float], ends: list[float]) -> list[Word]:
    """Group per-character timings into words, splitting at whitespace."""
    words, current, start, end = [], "", 0.0, 0.0
    for ch, s, e in zip(characters, starts, ends):
        if ch.isspace():
            if current:
                words.append((current, round(start, 3), round(end, 3)))
            current = ""
            continue
        if not current:
            start = s
        current += ch
        end = e
    if current:
        words.append((current, round(start, 3), round(end, 3)))
    return words


def _read_manifest(ep: Episode) -> dict:
    path = audio_dir(ep) / MANIFEST
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def plan_voice(ep: Episode, script: Script, voice_id: str, model: str, redo: set[int],
               settings: dict | None = None) -> list[LinePlan]:
    unknown = sorted(n for n in redo if n not in {s.n for s in script.shots})
    if unknown:
        raise PipelineError(f"--redo-voice: no shot {', '.join(map(str, unknown))} in this script")
    manifest = _read_manifest(ep)
    shots = script.shots
    plans = []
    for i, shot in enumerate(shots):
        path = audio_dir(ep) / f"{shot.n:02d}.mp3"
        fp = _fingerprint(shot.narration, voice_id, model, settings)
        entry = manifest.get(path.name, {})
        if shot.n in redo:
            reason = "--redo-voice"
        elif not path.exists():
            reason = "new"
        elif entry.get("fingerprint") != fp:
            reason = "changed since last read"
        elif "words" not in entry:
            reason = "no word timings yet"
        else:
            reason = ""
        moved = (find_moved(manifest, audio_dir(ep), path.name, fp, lambda e: "words" in e)
                 if reason and shot.n not in redo else "")
        if moved:
            reason = f"same as {moved} (copied, free)"
        plans.append(LinePlan(
            shot, path,
            previous_text=shots[i - 1].narration if i > 0 else "",
            next_text=shots[i + 1].narration if i + 1 < len(shots) else "",
            fingerprint=fp, reason=reason, copy_from=moved,
        ))
    return plans


def describe_voice_plan(plans: list[LinePlan]) -> str:
    todo = [p for p in plans if p.reason and not p.copy_from]
    lines = [f"Narration: {len(todo)} lines to read, {len(plans) - len(todo)} reused"]
    for p in plans:
        lines.append(f"  {p.path.name}  {p.reason or 'reuse'}  \"{p.shot.narration}\"")
    if todo:
        chars = sum(len(p.shot.narration) for p in todo)
        lines.append(f"ElevenLabs usage: about {chars} characters (counted against your plan's credits)")
    return "\n".join(lines)


def make_voice(client: VoiceClient, ep: Episode, plans: list[LinePlan],
               probe=None, pause: float = PAUSE_AFTER_LINE) -> list[tuple[int, float]]:
    """Read the planned lines. Returns (shot number, shot duration) for every shot."""
    probe = probe or audio_seconds
    out = audio_dir(ep)
    out.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(ep)
    for p in plans:
        if not p.reason or p.copy_from:
            continue
        speech = client.speak(p.shot.narration, p.previous_text, p.next_text)
        if not speech.audio:
            raise PipelineError(f"The voice API returned no audio for shot {p.shot.n}.")
        p.path.write_bytes(speech.audio)
        seconds = probe(p.path)
        manifest[p.path.name] = {
            "fingerprint": p.fingerprint,
            "text": p.shot.narration,
            "voice_id": client.voice_id,
            "model": client.model,
            "characters": len(p.shot.narration),
            "line_seconds": round(seconds, 3),
            "words": speech.words,
        }
        # Saved after every line, so an interrupted run keeps (and won't re-pay for) what it read.
        (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if client.model != FAKE_MODEL:  # billed as characters against the plan's credits, not in dollars
            log_cost(ep, "voice", p.path.name, client.model, len(p.shot.narration), "characters", None)
        print(f"  {p.path.name}  ({p.reason})  {seconds:.2f}s")
    return [(p.shot.n, shot_seconds(manifest[p.path.name]["line_seconds"], _pause(p.shot, pause))) for p in plans]


def narration_timings(ep: Episode, script: Script,
                      pause: float = PAUSE_AFTER_LINE) -> dict[int, tuple[float, float]]:
    """Shot number -> (line seconds, shot seconds). Fails if any line is missing or stale."""
    manifest = _read_manifest(ep)
    timings = {}
    for shot in script.shots:
        entry = manifest.get(f"{shot.n:02d}.mp3")
        if not entry or entry.get("text") != shot.narration or not (audio_dir(ep) / f"{shot.n:02d}.mp3").exists():
            raise PipelineError(f"Narration for shot {shot.n} is missing or out of date. Run the voice step first.")
        timings[shot.n] = (entry["line_seconds"], shot_seconds(entry["line_seconds"], _pause(shot, pause)))
    return timings


def shot_durations(ep: Episode, script: Script, pause: float = PAUSE_AFTER_LINE) -> dict[int, float]:
    """Shot number -> seconds, for the motion and assembly steps."""
    return {n: shot for n, (_, shot) in narration_timings(ep, script, pause).items()}


def word_timings(ep: Episode, script: Script) -> dict[int, list[Word] | None]:
    """Shot number -> when each word of its line is spoken, or None where no timings were recorded."""
    manifest = _read_manifest(ep)
    out = {}
    for shot in script.shots:
        words = manifest.get(f"{shot.n:02d}.mp3", {}).get("words")
        out[shot.n] = [tuple(w) for w in words] if words else None
    return out


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
    def __init__(self, api_key: str, voice_id: str, model: str, settings: dict | None = None):
        from elevenlabs.client import ElevenLabs

        # Key passed explicitly, so the SDK never falls back to the shell environment.
        self._client = ElevenLabs(api_key=api_key, timeout=120)
        self.voice_id = voice_id
        self.model = model
        self.settings = settings or None

    def speak(self, text: str, previous_text: str, next_text: str) -> Speech:
        from elevenlabs.core.api_error import ApiError
        from elevenlabs.types import VoiceSettings

        extra = {"voice_settings": VoiceSettings(**self.settings)} if self.settings else {}
        try:
            # The with-timestamps endpoint returns the audio plus when each character is spoken.
            reply = self._client.text_to_speech.convert_with_timestamps(
                self.voice_id, text=text, model_id=self.model, output_format=OUTPUT_FORMAT,
                previous_text=previous_text or None, next_text=next_text or None,
                request_options={"max_retries": 4},  # backoff on rate limits and server errors
                **extra,
            )
            a = reply.alignment
            words = words_from_alignment(a.characters, a.character_start_times_seconds,
                                         a.character_end_times_seconds) if a else None
            return Speech(base64.b64decode(reply.audio_base_64), words or None)
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

    def speak(self, text: str, previous_text: str, next_text: str) -> Speech:
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
            audio = out.read_bytes()
        words = text.split()  # spread evenly, so word-by-word captions can be tested for free
        step = seconds / len(words) if words else 0
        return Speech(audio, [(w, round(i * step, 3), round((i + 1) * step, 3)) for i, w in enumerate(words)])


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
    voice_settings = series.config.voice_settings.as_dict() if series.config.voice_settings else None
    return ElevenLabsVoiceClient(settings.elevenlabs_api_key, series.config.voice_id, settings.voice_model,
                                 voice_settings)
