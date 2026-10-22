"""Step 5: join the clips, narration, music, ambience, captions and end card into final.mp4.

One ffmpeg pass: clips joined with hard cuts; each narration line padded to its shot's length so
it starts exactly at the shot's first frame; music and an optional ambience bed (sfx/) each start at
a fixed random point in their track, faded in and out underneath. Levels are measured, not guessed:
the narration is brought to a standard loudness and each bed is set a fixed number of dB under it
(music_volume_db, sfx_volume_db), whatever the track's own mastering. Captions are laid over
as one stream of PNGs and the end card on top. final.json records a fingerprint of all inputs, so
an unchanged episode isn't re-encoded. playback_speed then speeds up the whole result in the same
pass (pictures, captions, narration and music alike; atempo keeps the pitch).
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
from .ffmpeg import FPS, frames, loudness, run_ffmpeg
from .motion import ClipPlan
from .schema import Script
from .series import Series
from .voice import audio_seconds

AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav"}
MUSIC_FADE_SECONDS = 2.0
NARRATION_LUFS = -16.0  # standard loudness for spoken social video; beds are set relative to it
PEAK_LIMIT = 0.89  # about -1 dBFS, so the mix never clips
FINAL_VERSION = 3  # bump when assembly's output changes, so finals are rebuilt


@dataclass
class Assembly:
    final: Path
    seconds: float
    music: Path | None
    font: Path | None
    rebuilt: bool
    sfx: Path | None = None


@dataclass(frozen=True)
class Bed:
    """A looping background track (music or ambience) and where in it this episode starts."""

    path: Path
    offset: float
    gain_db: float  # applied to the track so it sits at its level under the narration


def _pick_track(folders: list[Path], seed: str) -> Path | None:
    for folder in folders:
        if folder.is_dir():
            tracks = sorted(f for f in folder.iterdir()
                            if f.suffix.lower() in AUDIO_SUFFIXES and not f.name.startswith("."))
            if tracks:
                return random.Random(seed).choice(tracks)
    return None


MUSIC_CHOICE = "music.txt"  # in the episode folder: a track chosen with --music
SFX_CHOICE = "sfx.txt"  # in the episode folder: an ambience loop chosen with --sfx


def music_folders(series: Series, root: Path) -> list[Path]:
    return [series.path / "music", root / "assets" / "music"]


def sfx_folders(series: Series, root: Path) -> list[Path]:
    return [series.path / "sfx", root / "assets" / "sfx"]


def _chosen_track(folders: list[Path], choice: Path, what: str, flag: str) -> Path | None:
    """The track named in an episode's choice file, or None when nothing was chosen."""
    if not choice.is_file():
        return None
    name = choice.read_text(encoding="utf-8").strip()
    for folder in folders:
        if (folder / name).is_file():
            return folder / name
    raise PipelineError(f"The {what} chosen for this episode ({name}) is no longer in "
                        f"{' or '.join(str(f) for f in folders)}. Choose again with {flag}.")


def _choose_track(folders: list[Path], choice: Path, query: str) -> Path:
    """Save the one track whose file name contains `query` (any case) in the choice file."""
    tracks = sorted({f for folder in folders if folder.is_dir()
                     for f in folder.iterdir() if f.suffix.lower() in AUDIO_SUFFIXES and not f.name.startswith(".")})
    matches = [t for t in tracks if query.lower() in t.name.lower()]
    if len(matches) != 1:
        listing = "\n".join(f"  {t.name}" for t in tracks) or "  (no tracks found)"
        what = "No track" if not matches else f"{len(matches)} tracks"
        raise PipelineError(f"{what} match '{query}'. Available:\n{listing}")
    choice.parent.mkdir(parents=True, exist_ok=True)
    choice.write_text(matches[0].name + "\n", encoding="utf-8")
    return matches[0]


def _choice_path(series: Series, episode: int, name: str) -> Path:
    return series.episodes_dir / f"ep{episode:02d}" / name


def pick_music(series: Series, root: Path, episode: int) -> Path | None:
    """The track chosen with --music, else one from the series' music/ (or assets/music/) picked at
    random, the same one every run of this episode."""
    folders = music_folders(series, root)
    return (_chosen_track(folders, _choice_path(series, episode, MUSIC_CHOICE), "music", "--music")
            or _pick_track(folders, f"{series.name}-{episode}"))


def choose_music(series: Series, root: Path, episode: int, query: str) -> Path:
    """Save the one track whose file name contains `query` (any case) as this episode's music."""
    return _choose_track(music_folders(series, root), _choice_path(series, episode, MUSIC_CHOICE), query)


def pick_sfx(series: Series, root: Path, episode: int) -> Path | None:
    """The loop chosen with --sfx, else an ambience loop from the series' sfx/ (or assets/sfx/),
    the same one every run of this episode."""
    folders = sfx_folders(series, root)
    return (_chosen_track(folders, _choice_path(series, episode, SFX_CHOICE), "ambience", "--sfx")
            or _pick_track(folders, f"{series.name}-{episode}-sfx"))


def choose_sfx(series: Series, root: Path, episode: int, query: str) -> Path:
    """Save the one ambience loop whose file name contains `query` (any case) for this episode."""
    return _choose_track(sfx_folders(series, root), _choice_path(series, episode, SFX_CHOICE), query)


def start_offset(track_seconds: float, needed: float, seed: str) -> float:
    """Where to start in a track: a fixed random point that leaves enough track to play without
    looping (skipping long quiet intros on long tracks), or 0 when the track is too short."""
    room = track_seconds - needed - MUSIC_FADE_SECONDS
    return round(random.Random(seed).uniform(0, room), 3) if room > 0 else 0.0


def bed_gain(measured: float | None, level_db: float) -> float:
    """Gain that puts a bed `level_db` under the standard narration loudness."""
    return round(NARRATION_LUFS + level_db - measured, 2) if measured is not None else level_db


def _bed(path: Path | None, needed: float, seed: str, level_db: float, probe, measure) -> Bed | None:
    if path is None:
        return None
    track_seconds = probe(path)
    offset = start_offset(track_seconds, needed, seed)
    heard = measure([path], offset, needed) if offset else measure([path])  # a looped track: all of it
    return Bed(path, offset, bed_gain(heard, level_db))


def _fingerprint(clips: list[ClipPlan], script: Script, timings: dict, words: dict, audio: list[Path],
                 beds: list[Bed], font: Path | None, series: Series, narration_gain: float) -> str:
    h = hashlib.sha256()
    h.update(json.dumps({
        "clips": [c.fingerprint for c in clips],
        "lines": [[s.narration, *timings[s.n], words.get(s.n)] for s in script.shots],
        "end_card": script.end_card,
        "cta": series.config.end_card_cta,
        "beds": [[b.path.name, b.offset, b.gain_db] for b in beds],
        "narration_gain": narration_gain,
        "caption_style": series.config.caption_style,
        "fade_in": series.config.music_fade_in_seconds,
        "speed": series.config.playback_speed,
        "v": FINAL_VERSION,
    }).encode())
    for p in [*audio, *(b.path for b in beds), *([font] if font else [])]:
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def assemble(series: Series, ep: Episode, script: Script, clips: list[ClipPlan],
             timings: dict[int, tuple[float, float]], root: Path,
             words: dict[int, list | None] | None = None, probe=None, measure=None) -> Assembly:
    probe = probe or audio_seconds
    measure = measure or loudness
    words = words or {}
    cfg = series.config
    style = caption_style(cfg.caption_style)
    font = find_font(root / "assets" / "fonts", cfg.caption_font)
    shot_seconds = [frames(timings[s.n][1]) / FPS for s in script.shots]
    total = sum(shot_seconds)  # before playback_speed; captions and beds are laid out on this timeline
    speed = cfg.playback_speed
    seed = f"{series.name}-{ep.number}"
    music = _bed(pick_music(series, root, ep.number), total, seed + "-music-start", cfg.music_volume_db,
                 probe, measure)
    sfx = _bed(pick_sfx(series, root, ep.number), total, seed + "-sfx-start", cfg.sfx_volume_db, probe, measure)
    beds = [b for b in (music, sfx) if b]
    final = ep.path / "final.mp4"
    record_path = ep.path / "final.json"
    result = Assembly(final, total / speed, music.path if music else None, font, rebuilt=False,
                      sfx=sfx.path if sfx else None)

    audio = [ep.path / "audio" / f"{s.n:02d}.mp3" for s in script.shots]
    spoken = measure(audio)
    narration_gain = round(NARRATION_LUFS - spoken, 2) if spoken is not None else 0.0
    fp = _fingerprint(clips, script, timings, words, audio, beds, font, series, narration_gain)
    if final.exists() and record_path.is_file():
        if json.loads(record_path.read_text(encoding="utf-8")).get("fingerprint") == fp:
            return result

    captions, end_card = build_overlays(
        [(s.narration, timings[s.n][0], secs, words.get(s.n)) for s, secs in zip(script.shots, shot_seconds)],
        script.end_card, style, font, ep.path / "captions", end_card_cta(cfg.end_card_cta, ep.number),
    )
    concat_list = ep.path / "clips" / "concat.txt"
    concat_list.write_text("".join(f"file '{c.path.name}'\n" for c in clips), encoding="utf-8")
    caption_list = _caption_stream(captions, total, ep.path / "captions")

    args = ["-f", "concat", "-safe", "0", "-i", str(concat_list)]
    for a in audio:
        args += ["-i", str(a)]
    bed_inputs = []
    for k, b in enumerate(beds):
        bed_inputs.append(1 + len(audio) + k)
        # A track long enough is played from its offset without looping; a short one loops from the start.
        args += (["-ss", f"{b.offset:.3f}"] if b.offset else ["-stream_loop", "-1"]) + ["-i", str(b.path)]
    caption_input = 1 + len(audio) + len(beds)
    args += ["-f", "concat", "-safe", "0", "-i", str(caption_list), "-i", str(end_card.png)]

    graph = (_video_graph(caption_input, end_card, speed)
             + _audio_graph(audio, shot_seconds, list(zip(bed_inputs, beds)), total, narration_gain,
                          cfg.music_fade_in_seconds, speed))
    tmp = final.with_name("final.tmp.mp4")
    args += [
        "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
        "-t", f"{total / speed:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart", str(tmp),
    ]
    run_ffmpeg(args, "assembling final.mp4")
    tmp.replace(final)
    record_path.write_text(json.dumps({
        "fingerprint": fp, "seconds": round(total / speed, 3), "playback_speed": speed,
        "narration_gain_db": narration_gain,
        "music": music.path.name if music else None, "music_start": music.offset if music else None,
        "music_gain_db": music.gain_db if music else None,
        "sfx": sfx.path.name if sfx else None, "sfx_start": sfx.offset if sfx else None,
        "sfx_gain_db": sfx.gain_db if sfx else None,
        "font": font.name if font else None,
    }, indent=2) + "\n", encoding="utf-8")
    result.rebuilt = True
    return result


def _caption_stream(captions: list[Overlay], total: float, folder: Path) -> Path:
    """An ffconcat list showing each caption PNG for its time, with a blank frame in any gaps.

    One input instead of one per caption keeps the ffmpeg graph small however many words there are.
    """
    from PIL import Image

    from .ffmpeg import HEIGHT, WIDTH

    blank = folder / "blank.png"
    Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0)).save(blank)
    entries, t = [], 0.0
    for c in captions:
        if c.start > t + 0.001:
            entries.append((blank, c.start - t))
        entries.append((c.png, c.end - c.start))
        t = c.end
    if total > t + 0.001:
        entries.append((blank, total - t))
    lines = ["ffconcat version 1.0"]
    for png, secs in entries:
        lines += [f"file '{png.name}'", f"duration {secs:.3f}"]
    lines.append(f"file '{entries[-1][0].name}'" if entries else f"file '{blank.name}'")  # holds the last one
    path = folder / "captions.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _video_graph(caption_input: int, end_card: Overlay, speed: float = 1.0) -> list[str]:
    tempo = f"setpts=PTS/{speed},fps={FPS}," if speed != 1 else ""
    return [
        f"[0:v][{caption_input}:v]overlay=0:0:eof_action=pass[vc]",
        f"[vc][{caption_input + 1}:v]overlay=0:0:enable='between(t,{end_card.start:.3f},{end_card.end:.3f})'[ve]",
        f"[ve]{tempo}format=yuv420p[v]",
    ]


def _audio_graph(audio: list[Path], shot_seconds: list[float], beds: list[tuple[int, Bed]],
                 total: float, narration_gain: float = 0.0, fade_in: float = 1.0, speed: float = 1.0) -> list[str]:
    graph = []
    for i, secs in enumerate(shot_seconds):
        # Pad (or trim) each line to exactly its shot's length, so line i starts with shot i.
        graph.append(f"[{1 + i}:a]aresample=44100,aformat=channel_layouts=stereo,"
                     f"apad=whole_dur={secs:.3f},atrim=0:{secs:.3f}[n{i}]")
    graph.append("".join(f"[n{i}]" for i in range(len(audio)))
                 + f"concat=n={len(audio)}:v=0:a=1,volume={narration_gain}dB[narr]")
    limiter = f"alimiter=limit={PEAK_LIMIT}:level=disabled"
    if speed != 1:
        limiter = f"atempo={speed}," + limiter  # atempo takes 0.5-2.0, the range playback_speed allows
    if not beds:
        graph.append(f"[narr]{limiter}[a]")
        return graph
    fade_start = max(total - MUSIC_FADE_SECONDS, 0)
    fade = f"afade=t=in:st=0:d={fade_in}," if fade_in > 0 else ""  # 0: full level from the first frame
    for k, (index, bed) in enumerate(beds):
        graph.append(f"[{index}:a]aresample=44100,aformat=channel_layouts=stereo,atrim=0:{total:.3f},"
                     f"asetpts=PTS-STARTPTS,volume={bed.gain_db}dB,{fade}"
                     f"afade=t=out:st={fade_start:.3f}:d={MUSIC_FADE_SECONDS}[bed{k}]")
    labels = "[narr]" + "".join(f"[bed{k}]" for k in range(len(beds)))
    graph.append(f"{labels}amix=inputs={1 + len(beds)}:duration=first:normalize=0,{limiter}[a]")
    return graph


def end_card_cta(template: str, episode: int) -> str:
    return template.replace("{episode}", str(episode)).replace("{next}", str(episode + 1))


def length_warning(seconds: float, target: tuple[int, int]) -> str | None:
    low, high = target
    if seconds < low:
        return f"The episode is {seconds:.1f}s, under the {low}s target. Lengthen the narration."
    if seconds > high:
        return f"The episode is {seconds:.1f}s, over the {high}s target. Trim the narration."
    return None
