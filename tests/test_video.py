import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat

import make_episode
from pipeline.assemble import (NARRATION_LUFS, Bed, _audio_graph, bed_gain, choose_music, end_card_cta, length_warning,
                               pick_music, pick_sfx, start_offset)
from pipeline.captions import (CTA_STYLE, build_overlays, caption_style, find_font, phrase_times, render_text, render_words, split_phrases,
                               word_segments)
from pipeline.episode import Episode
from pipeline.errors import PipelineError
from pipeline.motion import plan_clips, render_drift, render_punchin, render_pushin, render_rock, rendered_motion
from pipeline.schema import validate_script
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


def test_word_segments_follow_the_spoken_words():
    words = [("Your", 0.0, 0.2), ("boat", 0.3, 0.5), ("is", 0.6, 0.7), ("gone.", 0.8, 1.2)]
    segs = word_segments("Your boat is gone.", words, start=10.0, shot_end=11.5)
    assert [(i, start, end) for _, i, start, end in segs] == [
        (0, 10.0, 10.3), (1, 10.3, 10.6), (2, 10.6, 10.8), (3, 10.8, 11.5)]
    assert segs[0][0] == ["Your", "boat", "is", "gone."]
    assert word_segments("Your boat is gone.", words[:3], 0, 2) is None  # mismatch: phrase timing instead
    assert word_segments("Your boat is gone.", None, 0, 2) is None


def test_highlighted_word_takes_the_highlight_colour(tmp_path):
    style = caption_style("highlight_word")
    out = tmp_path / "w.png"
    render_words(["Your", "boat", "is", "gone."], 1, style, None, out)
    colours = {c for _, c in Image.open(out).convert("RGB").getcolors(1 << 24)}
    assert style.highlight in colours and (255, 255, 255) in colours


def test_caption_font_can_be_named(tmp_path):
    (tmp_path / "A.ttf").write_bytes(b"x")
    (tmp_path / "B.ttf").write_bytes(b"x")
    assert find_font(tmp_path) == tmp_path / "A.ttf"
    assert find_font(tmp_path, "B.ttf") == tmp_path / "B.ttf"
    with pytest.raises(PipelineError, match="caption_font 'C.ttf'"):
        find_font(tmp_path, "C.ttf")


def test_music_start_point_is_fixed_and_leaves_room():
    assert start_offset(400, 65, "a") == start_offset(400, 65, "a")
    assert 0 <= start_offset(400, 65, "a") <= 400 - 65 - 2
    assert start_offset(400, 65, "a") != start_offset(400, 65, "b")
    assert start_offset(60, 65, "a") == 0.0  # too short: start at the top and loop


def test_beds_sit_a_fixed_level_under_the_narration():
    # A loud master (-10 LUFS) is turned down more than a quiet one (-30 LUFS), to the same level.
    assert NARRATION_LUFS + (-20) == -10 + bed_gain(-10, -20) == -30 + bed_gain(-30, -20)
    assert bed_gain(None, -20) == -20  # unmeasurable (silent): just the level


def test_music_can_be_chosen_per_episode(project, series_dir):
    series = load_series("test-series", project, 1)
    (project / "assets" / "music").mkdir(parents=True)
    for name in ("Calm Sea.mp3", "Storm Drums.mp3", "Storm Bells.mp3"):
        (project / "assets" / "music" / name).write_bytes(b"x")
    assert choose_music(series, project, 1, "calm").name == "Calm Sea.mp3"
    assert pick_music(series, project, 1).name == "Calm Sea.mp3"
    with pytest.raises(PipelineError, match="2 tracks match 'storm'"):
        choose_music(series, project, 1, "storm")
    with pytest.raises(PipelineError, match="No track match 'violin'"):
        choose_music(series, project, 1, "violin")
    (project / "assets" / "music" / "Calm Sea.mp3").unlink()
    with pytest.raises(PipelineError, match="no longer in"):
        pick_music(series, project, 1)


def test_sfx_comes_from_its_own_folder(project, series_dir):
    series = load_series("test-series", project, 1)
    assert pick_sfx(series, project, 1) is None
    (project / "assets" / "sfx").mkdir(parents=True)
    (project / "assets" / "sfx" / "waves.mp3").write_bytes(b"x")
    assert pick_sfx(series, project, 1) == project / "assets" / "sfx" / "waves.mp3"


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
    assert length_warning(65, (60, 70)) is None
    assert "under" in length_warning(49, (60, 70)) and "over" in length_warning(75, (60, 70))
    assert length_warning(45, (40, 70)) is None  # a series with shorter episodes


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
    assert record["music_start"] == 0.0  # a 5 s track is shorter than the episode: it loops from the top
    assert record["narration_gain_db"] == 0.0  # silent placeholder narration isn't boosted

    # Unchanged: nothing re-rendered or re-encoded.
    assert make_episode.run(base, root=project) == 0
    out = capsys.readouterr().out
    assert "nothing changed since the last build" in out and "Rendering clips" not in out

    # --redo 2 redraws shot 2's image, so only clip 2 is re-rendered and the final is rebuilt.
    assert make_episode.run(base + ["--redo", "2"], root=project) == 0
    out = capsys.readouterr().out
    assert "  02.mp4  " in out and "  01.mp4" not in out


@needs_ffmpeg
def test_word_captions_and_ambience_end_to_end(project, series_dir, good_script, monkeypatch):
    monkeypatch.setattr("pipeline.voice.FAKE_WORDS_PER_SECOND", 20)
    yaml_path = series_dir / "series.yaml"
    yaml_path.write_text(yaml_path.read_text().replace("caption_style: bold_white_outline",
                                                       "caption_style: highlight_word\nline_pause_seconds: 0.5"))
    sfx = project / "assets" / "sfx"
    sfx.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=120:a=0.2",
                    "-c:a", "libmp3lame", str(sfx / "waves.mp3")], check=True)
    series = load_series("test-series", project, 1)
    ep = Episode.of(series, 1)
    ep.save_script(good_script)
    ep.approve()
    assert make_episode.run(["--series", "test-series", "--episode", "1", "--fake-images", "--fake-voice"],
                            root=project) == 0
    record = json.loads((ep.path / "final.json").read_text())
    assert record["sfx"] == "waves.mp3" and record["sfx_start"] > 0  # long track: starts partway in
    loudness = subprocess.run(["ffmpeg", "-nostats", "-i", str(ep.path / "final.mp4"), "-af", "ebur128",
                               "-f", "null", "-"], capture_output=True, text=True).stderr
    level = float(loudness.rsplit("I:", 1)[1].split("LUFS")[0])
    assert level == pytest.approx(NARRATION_LUFS - 26, abs=1.5)  # silent narration: only the bed, at its level

    # The first word is highlighted at the very start of the video.
    frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.1", "-i", str(ep.path / "final.mp4"),
                            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           capture_output=True, check=True).stdout
    img = Image.frombytes("RGB", (1080, 1920), frame)
    r, g, b = caption_style("highlight_word").highlight
    yellow = sum(n for n, p in img.getcolors(1 << 24)
                 if abs(p[0] - r) < 30 and abs(p[1] - g) < 30 and abs(p[2] - b) < 40)
    assert yellow > 200
    words = sum(len(s["narration"].split()) for s in good_script["shots"])
    assert len(list((ep.path / "captions").glob("cap*.png"))) == words  # one caption per spoken word


def test_hook_motion_overrides_shot_one_only(project, good_script):
    series = load_series("test-series", project, 1)
    ep = Episode.of(series, 1)
    ep.save_script(good_script)
    (ep.path / "images").mkdir()
    for n in range(1, 11):
        Image.new("RGB", (8, 8)).save(ep.path / "images" / f"{n:02d}.png")
    script = validate_script(good_script, series, missing_sources_ok=True).script
    durations = {n: 2.0 for n in range(1, 11)}
    plain = plan_clips(ep, script, durations, set(), "rock")
    hooked = plan_clips(ep, script, durations, set(), "rock", "punchin")
    assert (hooked[0].motion, hooked[0].note) == ("punchin", "hook_motion")
    assert [c.motion for c in hooked[1:]] == [c.motion for c in plain[1:]]
    assert hooked[0].fingerprint != plain[0].fingerprint  # switching the hook re-renders shot 1
    assert (plain[4].motion, plain[4].note) == ("rock", "genvideo not built yet")


@needs_ffmpeg
def test_punchin_lands_early(tmp_path):
    img = Image.new("L", (1080, 1920), 0)
    for x in range(0, 1080, 60):  # vertical stripes: zooming in spreads them apart
        img.paste(255, (x, 0, x + 30, 1920))
    src = tmp_path / "stripes.png"
    img.convert("RGB").save(src)

    def moved(render):
        out = tmp_path / f"{render.__name__}.mp4"
        render(src, out, 30)
        f = _gray_frames(out)
        diff = lambda a, b: ImageStat.Stat(ImageChops.difference(a, b)).sum[0]
        return diff(f[0], f[10]), diff(f[19], f[29])

    early, late = moved(render_punchin)
    assert early > 3 * late  # front-loaded
    assert early > moved(render_pushin)[0]  # and faster than the push-in


def test_music_fade_in_can_be_off():
    bed = [(3, Bed(Path("m.mp3"), 0.0, -20))]
    assert "afade=t=in" in ";".join(_audio_graph([Path("a"), Path("b")], [1.0, 1.0], bed, 2.0, 0.0, 1.0))
    off = ";".join(_audio_graph([Path("a"), Path("b")], [1.0, 1.0], bed, 2.0, 0.0, 0))
    assert "afade=t=in" not in off and "afade=t=out" in off


def test_end_card_follow_line(tmp_path):
    assert end_card_cta("Follow for part {next}", 1) == "Follow for part 2"
    assert end_card_cta("Episode {episode}", 7) == "Episode 7"
    timings = [("Your boat is gone.", 1.0, 3.0, None)]
    _, plain = build_overlays(timings, "DAY 1", caption_style(""), None, tmp_path / "a")
    _, card = build_overlays(timings, "DAY 1", caption_style(""), None, tmp_path / "b", "Follow for part 2")
    r, g, b = CTA_STYLE.fill
    yellow = lambda path: sum(n for n, p in Image.open(path).getcolors(1 << 24) if p[:3] == (r, g, b) and p[3] == 255)
    assert yellow(plain.png) == 0 and yellow(card.png) > 500
