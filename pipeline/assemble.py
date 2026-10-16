"""Step 5: join the clips, narration, music, captions and end card into final.mp4.

One ffmpeg pass: clips joined with hard cuts; each narration line padded to its shot's length so
it starts exactly at the shot's first frame; music looped, lowered and faded out underneath;
caption and end-card PNGs overlaid at their times. final.json records a fingerprint of all inputs,
so an unchanged episode isn't re-encoded.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path

from .captions import Overlay, build_overlays, caption_style, find_font
from .episode import Episode
from .errors import PipelineError
from .ffmpeg import FPS, frames, run_ffmpeg
from .motion import ClipPlan
from .schema import Script
from .series import Series

MUSIC_SUFFIXES = {".mp3", ".m4a", ".wav"}
MUSIC_FADE_SECONDS = 2.0
TARGET_SECONDS = (25, 45)
FINAL_VERSION = 1  # bump when assembly's output changes, so finals are rebuilt


@dataclass
class Assembly:
    final: Path
    seconds: float
    music: Path | None
    font: Path | None
    rebuilt: bool


def pick_music(series: Series, root: Path, episode: int) -> Path | None:
    """A track from the series' music/ (or assets/music/), the same one every run of this episode."""
    for folder in (series.path / "music", root / "assets" / "music"):
        if folder.is_dir():
            tracks = sorted(f for f in folder.iterdir() if f.suffix.lower() in MUSIC_SUFFIXES and not f.name.startswith("."))
            if tracks:
                return random.Random(f"{series.name}-{episode}").choice(tracks)
    return None


def _fingerprint(clips: list[ClipPlan], script: Script, timings: dict, audio: list[Path],
                 music: Path | None, font: Path | None, series: Series) -> str:
    h = hashlib.sha256()
    h.update(json.dumps({
        "clips": [c.fingerprint for c in clips],
        "lines": [[s.narration, *timings[s.n]] for s in script.shots],
        "end_card": script.end_card,
        "music": music.name if music else None,
        "music_db": series.config.music_volume_db,
        "caption_style": series.config.caption_style,
        "v": FINAL_VERSION,
    }).encode())
    for p in [*audio, *(f for f in (music, font) if f)]:
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def assemble(series: Series, ep: Episode, script: Script, clips: list[ClipPlan],
             timings: dict[int, tuple[float, float]], root: Path) -> Assembly:
    style = caption_style(series.config.caption_style)
    music = pick_music(series, root, ep.number)
    font = find_font(root / "assets" / "fonts")
    shot_seconds = [frames(timings[s.n][1]) / FPS for s in script.shots]
    total = sum(shot_seconds)
    final = ep.path / "final.mp4"
    record_path = ep.path / "final.json"

    audio = [ep.path / "audio" / f"{s.n:02d}.mp3" for s in script.shots]
    fp = _fingerprint(clips, script, timings, audio, music, font, series)
    if final.exists() and record_path.is_file():
        if json.loads(record_path.read_text(encoding="utf-8")).get("fingerprint") == fp:
            return Assembly(final, total, music, font, rebuilt=False)

    overlays = build_overlays(
        [(s.narration, timings[s.n][0], secs) for s, secs in zip(script.shots, shot_seconds)],
        script.end_card, style, font, ep.path / "captions",
    )
    concat_list = ep.path / "clips" / "concat.txt"
    concat_list.write_text("".join(f"file '{c.path.name}'\n" for c in clips), encoding="utf-8")

    args = ["-f", "concat", "-safe", "0", "-i", str(concat_list)]
    for a in audio:
        args += ["-i", str(a)]
    music_index = None
    if music:
        music_index = 1 + len(audio)
        args += ["-stream_loop", "-1", "-i", str(music)]
    first_overlay = 1 + len(audio) + (1 if music else 0)
    for o in overlays:
        args += ["-i", str(o.png)]

    graph = _video_graph(overlays, first_overlay) + _audio_graph(audio, shot_seconds, music_index, total, series)
    tmp = final.with_name("final.tmp.mp4")
    args += [
        "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
        "-t", f"{total:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart", str(tmp),
    ]
    run_ffmpeg(args, "assembling final.mp4")
    tmp.replace(final)
    record_path.write_text(json.dumps({
        "fingerprint": fp, "seconds": round(total, 3),
        "music": music.name if music else None, "font": font.name if font else None,
    }, indent=2) + "\n", encoding="utf-8")
    return Assembly(final, total, music, font, rebuilt=True)


def _video_graph(overlays: list[Overlay], first_input: int) -> list[str]:
    graph, last = [], "0:v"
    for i, o in enumerate(overlays):
        out = f"v{i}"
        graph.append(f"[{last}][{first_input + i}:v]overlay=0:0:enable='between(t,{o.start:.3f},{o.end:.3f})'[{out}]")
        last = out
    graph.append(f"[{last}]format=yuv420p[v]")
    return graph


def _audio_graph(audio: list[Path], shot_seconds: list[float], music_index: int | None,
                 total: float, series: Series) -> list[str]:
    graph = []
    for i, secs in enumerate(shot_seconds):
        # Pad (or trim) each line to exactly its shot's length, so line i starts with shot i.
        graph.append(f"[{1 + i}:a]aresample=44100,aformat=channel_layouts=stereo,"
                     f"apad=whole_dur={secs:.3f},atrim=0:{secs:.3f}[n{i}]")
    graph.append("".join(f"[n{i}]" for i in range(len(audio))) + f"concat=n={len(audio)}:v=0:a=1[narr]")
    if music_index is None:
        graph.append("[narr]anull[a]")
        return graph
    fade_start = max(total - MUSIC_FADE_SECONDS, 0)
    graph.append(f"[{music_index}:a]aresample=44100,aformat=channel_layouts=stereo,atrim=0:{total:.3f},"
                 f"volume={series.config.music_volume_db}dB,"
                 f"afade=t=out:st={fade_start:.3f}:d={MUSIC_FADE_SECONDS}[mus]")
    graph.append("[narr][mus]amix=inputs=2:duration=first:normalize=0[a]")
    return graph


def length_warning(seconds: float) -> str | None:
    low, high = TARGET_SECONDS
    if seconds < low:
        return f"The episode is {seconds:.1f}s, under the {low}s target. Lengthen the narration."
    if seconds > high:
        return f"The episode is {seconds:.1f}s, over the {high}s target. Trim the narration."
    return None
