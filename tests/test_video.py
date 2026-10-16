import json
import shutil
import subprocess

import pytest
from PIL import Image

import make_episode
from pipeline.assemble import length_warning, pick_music
from pipeline.captions import caption_style, phrase_times, render_text, split_phrases
from pipeline.episode import Episode
from pipeline.errors import PipelineError
from pipeline.motion import render_drift, render_rock, rendered_motion
from pipeline.series import load_series

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")


def test_phrases_are_short_and_break_after_punctuation():
    assert split_phrases("Your boat is gone.") == ["Your boat is gone."]
    assert split_phrases("Don't. Seawater is saltier than your kidneys can handle.") == [
        "Don't.", "Seawater is", "saltier than your", "kidneys can handle."]
    assert split_phrases("Wait... what?") == ["Wait...", "what?"]
    for phrase in split_phrases("To flush the salt, your body burns water it doesn't have."):
        assert len(phrase.split()) <= 4


def test_phrase_times_fill_the_line_and_hold_to_shot_end():
    times = phrase_times(["one two", "three four five"], start=10.0, line_seconds=2.0, shot_end=12.3)
    assert times[0][0] == 10.0 and times[0][1] == times[1][0]
    assert times[1][1] == 12.3
    assert times[0][1] - 10.0 < 1.0  # the shorter phrase gets the smaller share


def test_unknown_caption_style():
    assert caption_style("") == caption_style("bold_white_outline")
    with pytest.raises(PipelineError, match="bold_white_outline"):
        caption_style("comic_sans")


def test_caption_png_is_a_transparent_full_frame(tmp_path):
    out = tmp_path / "c.png"
    render_text("Your boat is gone.", caption_style(""), None, out)
    img = Image.open(out)
    assert img.size == (1080, 1920) and img.mode == "RGBA"
    alpha = img.getchannel("A")
    assert alpha.getpixel((0, 0)) == 0 and alpha.getbbox() is not None


def test_music_prefers_series_folder_and_is_stable(project, series_dir):
    series = load_series("test-series", project, 1)
    assert pick_music(series, project, 1) is None
    (project / "assets" / "music").mkdir(parents=True)
    for name in ("a.mp3", "b.mp3", "c.mp3"):
        (project / "assets" / "music" / name).write_bytes(b"x")
    first = pick_music(series, project, 1)
    assert first.parent == project / "assets" / "music" and pick_music(series, project, 1) == first
    (series_dir / "music").mkdir()
    (series_dir / "music" / "theme.mp3").write_bytes(b"x")
    assert pick_music(series, project, 1) == series_dir / "music" / "theme.mp3"


def test_length_warning():
    assert length_warning(30) is None
    assert "under" in length_warning(20) and "over" in length_warning(50)


def test_genvideo_uses_the_default_motion_until_built():
    assert rendered_motion("rock", "drift") == "rock" and rendered_motion("drift", "rock") == "drift"
    assert rendered_motion("genvideo", "rock") == "rock"


def _gray_frames(path, w=1080, h=1920):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return [Image.frombytes("L", (w, h), raw[i:i + w * h]) for i in range(0, len(raw), w * h)]


@needs_ffmpeg
def test_rock_never_shows_corners(tmp_path):
    grey = tmp_path / "grey.png"
    Image.new("RGB", (1080, 1920), (128, 128, 128)).save(grey)
    render_rock(grey, tmp_path / "rock.mp4", 90, shot_n=1)  # 3 s: one full swing
    frames = _gray_frames(tmp_path / "rock.mp4")
    assert len(frames) == 90
    edges = [(0, 0), (1079, 0), (0, 1919), (1079, 1919), (540, 0), (540, 1919), (0, 960), (1079, 960)]
    assert min(f.getpixel(p) for f in frames for p in edges) > 100  # black would mean a corner showed


@needs_ffmpeg
def test_drift_pans_opposite_ways_on_alternate_shots(tmp_path):
    img = tmp_path / "ramp.png"
    Image.linear_gradient("L").rotate(90).resize((1080, 1920)).convert("RGB").save(img)  # brightness varies across x

    def brightness_change(shot_n):
        render_drift(img, tmp_path / "d.mp4", 30, shot_n=shot_n)
        frames = _gray_frames(tmp_path / "d.mp4")
        return frames[-1].getpixel((540, 960)) - frames[0].getpixel((540, 960))

    one, two = brightness_change(1), brightness_change(2)
    assert abs(one) > 10 and abs(two) > 10 and (one > 0) != (two > 0)


def test_video_waits_for_narration(project, good_script, capsys):
    series = load_series("test-series", project, 1)
    ep = Episode.of(series, 1)
    ep.save_script(good_script)
    ep.approve()
    assert make_episode.run(["--series", "test-series", "--episode", "1", "--fake-images"], root=project) == 0
    assert "Video: waits for narration" in capsys.readouterr().out
    assert not (ep.path / "final.mp4").exists()


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,"
                          "r_frame_rate,duration", "-of", "json", str(path)], capture_output=True, text=True).stdout
    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


@needs_ffmpeg
def test_full_video_end_to_end(project, good_script, capsys, monkeypatch):
    monkeypatch.setattr("pipeline.voice.FAKE_WORDS_PER_SECOND", 20)  # short clips keep the test quick
    music = project / "assets" / "music"
    music.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=5",
                    "-c:a", "libmp3lame", str(music / "tone.mp3")], check=True)
    series = load_series("test-series", project, 1)
    ep = Episode.of(series, 1)
    ep.save_script(good_script)
    ep.approve()
    base = ["--series", "test-series", "--episode", "1", "--fake-images", "--fake-voice"]

    assert make_episode.run(base, root=project) == 0
    final = ep.path / "final.mp4"
    streams = _probe(final)
    v, a = streams["video"], streams["audio"]
    assert (v["codec_name"], v["width"], v["height"], v["r_frame_rate"]) == ("h264", 1080, 1920, "30/1")
    assert a["codec_name"] == "aac"
    record = json.loads((ep.path / "final.json").read_text())
    assert record["music"] == "tone.mp3"
    assert float(v["duration"]) == pytest.approx(record["seconds"], abs=0.05)
    assert float(a["duration"]) == pytest.approx(record["seconds"], abs=0.05)
    out = capsys.readouterr().out
    assert "genvideo not built yet" in out and "Done:" in out

    # Unchanged: nothing re-rendered or re-encoded.
    assert make_episode.run(base, root=project) == 0
    out = capsys.readouterr().out
    assert "nothing changed since the last build" in out and "Rendering clips" not in out

    # --redo 2 redraws shot 2's image, so only clip 2 is re-rendered and the final is rebuilt.
    assert make_episode.run(base + ["--redo", "2"], root=project) == 0
    out = capsys.readouterr().out
    assert "  02.mp4  " in out and "  01.mp4" not in out
