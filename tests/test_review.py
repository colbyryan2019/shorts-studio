import argparse
import json
import shutil

import pytest
import yaml
from PIL import Image

import make_episode
import new_series
from pipeline.config import PROJECT_ROOT
from pipeline.cost import format_step, log_cost, read_costs, summarize
from pipeline.episode import Episode
from pipeline.errors import PipelineError
from pipeline.imagegen import FakeImageClient, GeneratedImage, make_budget
from pipeline.images import make_images, plan_images
from pipeline.review import write_review
from pipeline.schema import validate_script
from pipeline.series import load_series

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")


@pytest.fixture
def episode(project, good_script):
    series = load_series("test-series", project, 1)
    ep = Episode.of(series, 1)
    ep.save_script(good_script)
    return series, ep, validate_script(good_script, series, missing_sources_ok=True).script


# --- --redo shot lists -------------------------------------------------------------

def test_shot_lists():
    assert make_episode.shot_numbers("5") == (5,)
    assert make_episode.shot_numbers("3, 7") == (3, 7)
    assert make_episode.shot_numbers("2-4,9") == (2, 3, 4, 9)
    assert make_episode.shot_numbers("ALL") is None
    for bad in ("0", "4-2", "x", "1,,2", ""):
        with pytest.raises(argparse.ArgumentTypeError):
            make_episode.shot_numbers(bad)


def test_redo_all_and_combined(episode):
    _, _, script = episode
    assert make_episode.expand_shots([(1,), (3, 4)], script) == {1, 3, 4}
    assert make_episode.expand_shots([(1,), None], script) == set(range(1, 11))


def test_redo_cant_be_mixed_with_script_changes():
    with pytest.raises(SystemExit):
        make_episode.parse_args(["--series", "s", "--episode", "1", "--approve", "--redo", "2"])


def test_redo_waits_for_approval(project, episode, capsys):
    assert make_episode.run(["--series", "test-series", "--episode", "1", "--redo", "2"], root=project) == 0
    assert "wait until the script is approved" in capsys.readouterr().out


# --- cost.json ---------------------------------------------------------------------

def test_cost_log_and_summary(episode):
    _, ep, _ = episode
    log_cost(ep, "images", "01.png", "gpt-image-2", 1, "image", 0.03)
    log_cost(ep, "images", "02.png", "gpt-image-2", 1, "image", 0.05)
    log_cost(ep, "voice", "01.mp3", "eleven", 40, "characters", None)
    entries = read_costs(ep)
    assert [e["item"] for e in entries] == ["01.png", "02.png", "01.mp3"]
    images, voice = summarize(entries)
    assert (images.step, images.calls, round(images.usd, 2), images.units) == ("images", 2, 0.08, {"image": 2})
    assert (voice.usd, voice.unpriced, voice.units) == (0, 1, {"characters": 40})
    assert format_step(images) == "images: $0.08 · 2 calls · 2 image"
    assert "not priced in USD" in format_step(voice)


def test_paid_images_are_logged_fakes_are_not(episode):
    series, ep, script = episode

    class Priced(FakeImageClient):
        def generate(self, prompt, size, refs):
            image = super().generate(prompt, size, refs)
            return GeneratedImage(image.png, output_tokens=1000)

    make_images(FakeImageClient(), series, ep, plan_images(series, ep, script, "fake", set()), make_budget("fake", ep.path))
    assert read_costs(ep) == []
    client = Priced(model="gpt-image-2")
    plans = plan_images(series, ep, script, client.model, set())  # a new model redraws every shot
    make_images(client, series, ep, plans, make_budget("fake", ep.path))
    entries = read_costs(ep)
    assert [e["item"] for e in entries] == [f"{n:02d}.png" for n in range(1, 11)]
    assert (entries[1]["step"], entries[1]["model"], entries[1]["amount_usd"]) == ("images", "gpt-image-2", 0.03)


# --- review.md ---------------------------------------------------------------------

def test_review_before_anything_is_rendered(episode):
    series, ep, script = episode
    review = write_review(series, ep, script, mode="stills", status="draft")
    text = review.path.read_text()
    assert text.startswith("# Day One\n")
    assert "script DRAFT" in text and "Video: not assembled yet." in text
    assert "Your boat is gone. No land." in text  # full narration as one passage
    assert "| 5 | The ocean is right there. Every instinct tells you to drink. | - | genvideo |" in text
    assert "1. Drinking seawater dehydrates you" in text and "`sources/survival-manual.txt`)" in text
    assert "No images yet." in text and review.contact_sheet is None
    assert "No paid calls logged" in text


def test_review_lists_rendered_motion_fallbacks_contact_sheet_and_cost(episode):
    series, ep, script = episode
    plans = plan_images(series, ep, script, "fake", set())
    make_images(FakeImageClient(), series, ep, plans, make_budget("fake", ep.path))
    clips = ep.path / "clips"
    clips.mkdir()
    (clips / "manifest.json").write_text(json.dumps({
        "05.mp4": {"motion": "rock", "seconds": 2.0, "fallback": "fal: 500 server error (3 attempts)"},
        "06.mp4": {"motion": "pushin", "seconds": 2.0},
    }))
    log_cost(ep, "images", "01.png", "gpt-image-2", 1, "image", 0.04)
    log_cost(ep, "voice", "01.mp3", "eleven", 18, "characters", None)

    review = write_review(series, ep, script, mode="stills", status="approved",
                          shot_seconds={s.n: 2.5 for s in script.shots})
    text = review.path.read_text()
    assert "| 5 | The ocean is right there. Every instinct tells you to drink. | 2.5 | genvideo → rock |" in text
    assert "| 6 | Don't. Seawater is saltier than your kidneys can handle. | 2.5 | pushin |" in text
    assert "- 05.mp4: used rock instead: fal: 500 server error (3 attempts)" in text
    assert "about 25.0 s of narration" in text
    assert "![Contact sheet](contact_sheet.png)" in text
    assert "**Total: $0.04** plus ElevenLabs characters" in text
    assert (review.total_usd, review.unpriced) == (0.04, 1)
    with Image.open(review.contact_sheet) as sheet:
        assert sheet.width == 8 + 5 * (216 + 8)  # five across
        assert sheet.height == 8 + 2 * (384 + 40 + 8)  # ten shots: two rows


@needs_ffmpeg
def test_full_run_writes_the_review(project, episode, capsys, monkeypatch):
    monkeypatch.setattr("pipeline.voice.FAKE_WORDS_PER_SECOND", 20)
    _, ep, _ = episode
    ep.approve()
    assert make_episode.run(["--series", "test-series", "--episode", "1", "--fake-images", "--fake-voice"],
                            root=project) == 0
    out = capsys.readouterr().out
    assert f"Review sheet: {ep.path / 'review.md'}" in out
    assert "Logged cost for this episode: none" in out  # fakes cost nothing and aren't logged
    text = (ep.path / "review.md").read_text()
    assert "Video: [final.mp4](final.mp4)" in text and "script APPROVED · visual mode: stills" in text
    assert "genvideo → rock" in text  # genvideo isn't built: rendered with the default motion

    # --redo 2-3 redraws two shots, so two clips are re-rendered.
    assert make_episode.run(["--series", "test-series", "--episode", "1", "--fake-images", "--fake-voice",
                             "--redo", "2-3"], root=project) == 0
    out = capsys.readouterr().out
    assert "  02.mp4  " in out and "  03.mp4  " in out and "  04.mp4" not in out


# --- new_series.py -----------------------------------------------------------------

def test_new_series_from_the_real_templates(project, good_script, capsys):
    templates = PROJECT_ROOT / "templates" / "series"
    assert new_series.run(["--name", "lost-in-the-desert"], root=project, templates=templates) == 0
    assert "Created" in capsys.readouterr().out
    path = project / "series" / "lost-in-the-desert"
    assert sorted(p.name for p in path.iterdir()) == [
        "bible.md", "episodes", "refs", "series.yaml", "sources", "style_line.txt"]
    assert 'title: "Lost In The Desert"' in (path / "series.yaml").read_text()
    assert "# Lost In The Desert: Series Bible" in (path / "bible.md").read_text()

    # Unfilled template lines stop every command with a clear message.
    with pytest.raises(PipelineError, match="series bible still has .* TODO line"):
        load_series("lost-in-the-desert", project, 1)
    bible = (path / "bible.md").read_text()
    (path / "bible.md").write_text("\n".join(l for l in bible.splitlines() if not l.startswith("TODO:")))
    with pytest.raises(PipelineError, match="style line still has 1 TODO line"):
        load_series("lost-in-the-desert", project, 1)
    (path / "style_line.txt").write_text("Flat paper-cut look.\n")

    # Filled in, it works with no code changes: the template's series.yaml is valid as is.
    series = load_series("lost-in-the-desert", project, 1)
    assert series.config.title == "Lost In The Desert"
    (path / "sources" / "survival-manual.txt").write_text("Never drink seawater.\n")
    series = load_series("lost-in-the-desert", project, 1)
    assert validate_script(good_script, series).script is not None


def test_new_series_title_and_checks(project):
    templates = PROJECT_ROOT / "templates" / "series"
    assert new_series.run(["--name", "deep-dark", "--title", "Deep: The Dark"], root=project, templates=templates) == 0
    assert yaml.safe_load((project / "series" / "deep-dark" / "series.yaml").read_text())["title"] == "Deep: The Dark"
    with pytest.raises(PipelineError, match="already exists"):
        new_series.run(["--name", "deep-dark"], root=project, templates=templates)
    with pytest.raises(PipelineError, match="already exists"):
        new_series.run(["--name", "test-series"], root=project, templates=templates)
    with pytest.raises(SystemExit):
        new_series.parse_args(["--name", "Bad Name"])


# --- shots that moved to a new number ----------------------------------------------

def test_inserted_shot_reuses_moved_images_and_lines(project, episode, capsys, monkeypatch, good_script):
    monkeypatch.setattr("pipeline.voice.FAKE_WORDS_PER_SECOND", 20)
    _, ep, _ = episode
    ep.approve()
    base = ["--series", "test-series", "--episode", "1", "--fake-images", "--fake-voice"]
    assert make_episode.run(base, root=project) == 0
    old_image = (ep.path / "images" / "01.png").read_bytes()
    capsys.readouterr()

    # A new hook goes in front (and the last shot goes, to stay in the limits): every shot moves down one.
    new_hook = {"n": 1, "narration": "You have three days to live.", "image_prompt": "A face, close up",
                "motion": "pushin", "refs": []}
    shots = [new_hook] + good_script["shots"][:-1]
    for n, shot in enumerate(shots, start=1):
        shot["n"] = n
    ep.save_script({**good_script, "shots": shots})
    ep.approve()
    assert make_episode.run(base, root=project) == 0
    out = capsys.readouterr().out
    assert "Images: 1 to draw, 9 reused" in out and "Narration: 1 lines to read, 9 reused" in out
    assert "02.png  same as 01.png (copied, free)" in out
    assert "Reused 9 image(s)" in out and "Reused 9 narration line(s)" in out
    assert (ep.path / "images" / "02.png").read_bytes() == old_image  # the old shot 1, not a redraw
    assert (ep.path / "images" / "01.png").read_bytes() != old_image

    # Everything is in place now: a re-run reuses all of it.
    assert make_episode.run(base, root=project) == 0
    out = capsys.readouterr().out
    assert "Images: 0 to draw, 10 reused" in out and "Narration: 0 lines to read, 10 reused" in out
