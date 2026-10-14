import io
import json

import pytest
from PIL import Image

import make_episode
import make_refs
from pipeline import refs
from pipeline.episode import Episode
from pipeline.errors import PipelineError
from pipeline.imagegen import FakeImageClient, GeneratedImage, fit_to_frame
from pipeline.images import make_images, plan_images
from pipeline.llm import FAKE_REFS, FakeLLMClient
from pipeline.schema import validate_script
from pipeline.script import build_system
from pipeline.series import load_series

REFS_YAML = """\
survivor:
  description: A stick figure in a wide-brim hat.
  keywords: [survivor, figure]
dinghy:
  description: A small grey dinghy.
  keywords: [dinghy]
"""


def png(size=(64, 64), color=(200, 0, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def with_refs(project, series_dir):
    """test-series with refs.yaml, a picked survivor image and the dinghy image from conftest."""
    (series_dir / "refs" / "refs.yaml").write_text(REFS_YAML)
    (series_dir / "refs" / "survivor.png").write_bytes(png())
    (series_dir / "refs" / "dinghy.png").write_bytes(png(color=(0, 0, 200)))
    return series_dir


def script_with_refs(good_script):
    for shot in good_script["shots"]:
        text = shot["image_prompt"].lower()
        shot["refs"] = [n for n, words in (("survivor", ["survivor", "figure"]), ("dinghy", ["dinghy"]))
                        if any(w in text for w in words)]
    return good_script


# --- refs.yaml and the script rule ------------------------------------------------

def test_shots_showing_a_ref_must_list_it(project, with_refs, good_script):
    series = load_series("test-series", project, 1)
    errors = validate_script(good_script, series).errors
    assert any("shot 2: image_prompt mentions 'dinghy'" in e and 'add "dinghy"' in e for e in errors)
    assert validate_script(script_with_refs(good_script), series).ok


def test_keywords_match_whole_words_only(project, with_refs, good_script):
    series = load_series("test-series", project, 1)
    good_script = script_with_refs(good_script)
    good_script["shots"][6]["image_prompt"] = "A figurehead carved on a ship"  # 'figure' inside a word
    assert validate_script(good_script, series).ok


def test_ref_without_image_is_a_warning(project, with_refs, good_script):
    (with_refs / "refs" / "survivor.png").unlink()
    series = load_series("test-series", project, 1)
    script = script_with_refs(good_script)
    for shot in script["shots"]:
        shot["refs"] = [r for r in shot["refs"] if r != "survivor"]
    result = validate_script(script, series)
    assert result.ok, result.errors  # no "must list survivor" errors while it has no image
    assert any("ref 'survivor' has no picked image" in w for w in result.warnings)


def test_script_prompt_describes_refs(project, with_refs):
    system = build_system(load_series("test-series", project, 1))
    assert "survivor: A stick figure in a wide-brim hat." in system
    assert "survivor, figure" in system


def test_bad_ref_name_in_yaml(project, series_dir):
    (series_dir / "refs" / "refs.yaml").write_text("Big Boat:\n  description: x\n  keywords: [boat]\n")
    with pytest.raises(PipelineError, match="lowercase"):
        load_series("test-series", project, 1)


# --- make_refs.py ---------------------------------------------------------------

def test_make_refs_propose_generate_pick(project, series_dir):
    base = ["--series", "test-series"]
    assert make_refs.run(base, root=project, llm=FakeLLMClient([FAKE_REFS])) == 0
    assert "survivor:" in (series_dir / "refs" / "refs.yaml").read_text()

    with pytest.raises(PipelineError, match="already exists"):
        make_refs.run(base + ["--propose"], root=project, llm=FakeLLMClient([FAKE_REFS]))

    class RealishClient(FakeImageClient):
        pass

    images = RealishClient(model="some-real-model")
    assert make_refs.run(base + ["--generate", "survivor"], root=project, images=images) == 0
    assert len(images.calls) == 3
    assert (series_dir / "refs" / "candidates" / "survivor-3.png").exists()
    assert "wide-brim hat" in images.calls[0][0]

    # Existing candidates aren't redrawn unless --force.
    make_refs.run(base + ["--generate", "survivor"], root=project, images=images)
    assert len(images.calls) == 3

    assert make_refs.run(base + ["--pick", "survivor", "2"], root=project) == 0
    picked = (series_dir / "refs" / "survivor.png").read_bytes()
    assert picked == (series_dir / "refs" / "candidates" / "survivor-2.png").read_bytes()


def test_fake_candidates_cannot_be_picked(project, series_dir):
    base = ["--series", "test-series"]
    make_refs.run(base, root=project, llm=FakeLLMClient([FAKE_REFS]))
    make_refs.run(base + ["--generate", "dinghy", "--fake-images"], root=project)
    with pytest.raises(PipelineError, match="placeholder"):
        make_refs.run(base + ["--pick", "dinghy", "1"], root=project)


def test_generate_dry_run_draws_nothing(project, series_dir, capsys):
    base = ["--series", "test-series"]
    make_refs.run(base, root=project, llm=FakeLLMClient([FAKE_REFS]))
    images = FakeImageClient()
    make_refs.run(base + ["--generate", "--dry-run"], root=project, images=images)
    assert images.calls == []
    assert "6 candidates to draw" in capsys.readouterr().out


# --- shot images and caching -------------------------------------------------------

@pytest.fixture
def episode(project, with_refs, good_script):
    series = load_series("test-series", project, 1)
    ep = Episode.of(series, 1)
    ep.save_script(script_with_refs(good_script))
    return series, ep


def _plan_and_draw(series, ep, client, redo=()):
    script = validate_script(ep.load_script_data(), series).script
    plans = plan_images(series, ep, script, client.model, set(redo))
    make_images(client, series, ep, plans)
    return plans


def test_images_are_cached_and_redrawn_only_when_inputs_change(episode, with_refs):
    series, ep = episode
    client = FakeImageClient()
    _plan_and_draw(series, ep, client)
    assert len(client.calls) == 10
    first = client.calls[1]
    assert first[1] == "1152x2048" and [p.name for p in first[2]] == ["dinghy.png"]
    assert "Stick figures." in first[0] and "image 1: the dinghy" in first[0]

    _plan_and_draw(series, ep, client)  # nothing changed
    assert len(client.calls) == 10

    data = ep.load_script_data()
    data["shots"][6]["image_prompt"] = "A different picture of the sea"
    ep.save_script(data)
    plans = _plan_and_draw(series, ep, client)
    assert len(client.calls) == 11
    assert [p.path.name for p in plans if p.reason] == ["07.png"]

    _plan_and_draw(series, ep, client, redo=[2, 3])
    assert len(client.calls) == 13

    # A new survivor reference redraws exactly the shots that use it.
    (with_refs / "refs" / "survivor.png").write_bytes(png(color=(0, 200, 0)))
    script = validate_script(ep.load_script_data(), series).script
    users = [f"{s.n:02d}.png" for s in script.shots if "survivor" in s.refs]
    plans = plan_images(series, ep, script, client.model, set())
    assert [p.path.name for p in plans if p.reason] == users


def test_switching_model_redraws_everything(episode):
    series, ep = episode
    _plan_and_draw(series, ep, FakeImageClient())
    script = validate_script(ep.load_script_data(), series).script
    assert all(p.reason for p in plan_images(series, ep, script, "real-model", set()))


def test_redo_unknown_shot(episode):
    series, ep = episode
    script = validate_script(ep.load_script_data(), series).script
    with pytest.raises(PipelineError, match="no shot 42"):
        plan_images(series, ep, script, "m", {42})


def test_fit_to_frame():
    assert fit_to_frame(png((1152, 2048))).size == (1080, 1920)
    padded = fit_to_frame(png((1024, 1536), color=(240, 236, 226)))
    assert padded.size == (1080, 1920)
    assert padded.getpixel((540, 5)) == (240, 236, 226)  # bars use the image's own edge color


def test_cost_from_tokens():
    image = GeneratedImage(png=b"", input_image_tokens=1000, input_text_tokens=100, output_tokens=2000)
    assert image.cost("gpt-image-2.5-sunburst") == pytest.approx((1000 * 8 + 100 * 5 + 2000 * 30) / 1e6)
    assert image.cost("unknown-model") is None


# --- make_episode.py --------------------------------------------------------------

def test_cli_images_need_approval_and_approve_stops(episode, project, capsys):
    series, ep = episode
    base = ["--series", "test-series", "--episode", "1"]
    client = FakeImageClient()
    make_episode.run(base, root=project, image_client=client)
    assert client.calls == []  # draft: nothing drawn

    assert make_episode.run(base + ["--approve"], root=project, image_client=client) == 0
    assert client.calls == []  # approving doesn't spend anything

    assert make_episode.run(base + ["--dry-run"], root=project, image_client=client) == 0
    assert client.calls == []
    assert "10 to draw" in capsys.readouterr().out

    assert make_episode.run(base, root=project, image_client=client) == 0
    assert len(client.calls) == 10
    manifest = json.loads((ep.path / "images" / "manifest.json").read_text())
    assert manifest["01.png"]["model"] == "fake"


def test_cli_refuses_images_when_approved_script_breaks_rules(project, series_dir, good_script):
    series = load_series("test-series", project, 1)
    ep = Episode.of(series, 1)
    ep.save_script(good_script)  # no refs listed
    ep.approve()
    (series_dir / "refs" / "refs.yaml").write_text(REFS_YAML)
    (series_dir / "refs" / "survivor.png").write_bytes(png())
    client = FakeImageClient()
    assert make_episode.run(["--series", "test-series", "--episode", "1"], root=project, image_client=client) == 1
    assert client.calls == []


def test_cli_image_settings_checked_before_drawing(episode, project):
    series, ep = episode
    ep.approve()
    with pytest.raises(PipelineError, match="OPENAI_API_KEY is not set"):
        make_episode.run(["--series", "test-series", "--episode", "1"], root=project)
