import json

import pytest

import make_episode
from pipeline.episode import Episode
from pipeline.errors import PipelineError
from pipeline.llm import FakeLLMClient
from pipeline.schema import validate_script
from pipeline.script import build_system, build_task, extract_json, write_script
from pipeline.series import load_series


@pytest.fixture
def series(project):
    return load_series("test-series", project, episode=1)


# --- validation ----------------------------------------------------------------

def test_fake_script_is_valid(series, good_script):
    result = validate_script(good_script, series)
    assert result.ok, result.errors


def test_wrong_motion_is_a_structure_error(series, good_script):
    good_script["shots"][2]["motion"] = "spin"
    result = validate_script(good_script, series)
    assert result.script is None
    assert any(e.startswith("shots[3].motion") for e in result.errors)


def test_extra_field_is_rejected(series, good_script):
    good_script["mood"] = "grim"
    assert not validate_script(good_script, series).ok


def test_word_count_limits(series, good_script):
    good_script["shots"] = good_script["shots"][:8]
    result = validate_script(good_script, series)
    assert any("words in total" in e for e in result.errors)


def test_shot_count_limits(series, good_script):
    extra = dict(good_script["shots"][0], narration="Wait.")
    for n in range(11, 14):
        good_script["shots"].append(dict(extra, n=n))
    result = validate_script(good_script, series)
    assert any("shot count is 13" in e for e in result.errors)


def test_shot_numbering(series, good_script):
    good_script["shots"][1]["n"] = 7
    assert any("numbered 1..10" in e for e in validate_script(good_script, series).errors)


def test_genvideo_cap(series, good_script):
    for shot in good_script["shots"][:3]:
        shot["motion"] = "genvideo"
    assert any("at most 2 allowed" in e for e in validate_script(good_script, series).errors)


def test_refs_must_exist(series, good_script):
    good_script["shots"][0]["refs"] = ["dinghy"]
    assert validate_script(good_script, series).ok
    good_script["shots"][0]["refs"] = ["survivor"]
    errors = validate_script(good_script, series).errors
    assert errors == ["shot 1: ref 'survivor' has no image in refs/. Available: dinghy"]


def test_claim_sources_must_exist(series, good_script):
    good_script["claims"][0]["source_file"] = "made-up.txt"
    result = validate_script(good_script, series)
    assert any("made-up.txt" in e for e in result.errors)

    relaxed = validate_script(good_script, series, missing_sources_ok=True)
    assert relaxed.ok and any("made-up.txt" in w for w in relaxed.warnings)


def test_claim_sources_not_checked_when_not_required(project, series_dir, good_script):
    yaml_path = series_dir / "series.yaml"
    yaml_path.write_text(yaml_path.read_text().replace("sources_required: true", "sources_required: false"))
    series = load_series("test-series", project, episode=1)
    good_script["claims"][0]["source_file"] = "made-up.txt"
    assert validate_script(good_script, series).ok


def test_extract_json_tolerates_fences():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("no json here")


# --- prompt and retry ------------------------------------------------------------

def test_prompt_contains_all_inputs(project, series_dir, good_script):
    d = series_dir / "episodes" / "ep01"
    d.mkdir(parents=True)
    (d / "script.json").write_text(json.dumps({"title": "Earlier Episode"}))
    series = load_series("test-series", project, episode=2)

    system = build_system(series)
    task = build_task(series, 2, "Rain finally comes.")
    assert "Second person only" in system          # house rules
    assert "lost at sea" in system                 # bible
    assert "Never drink seawater" in system        # sources
    assert "dinghy" in system                      # allowed refs
    assert "90-110 words" in system                # limits
    assert "60-70 seconds" in system               # default target length
    assert "Rain finally comes." in task           # outline
    assert "Earlier Episode" in task               # recent episodes


def test_retry_feeds_errors_back(series, good_script):
    bad = dict(good_script, shots=good_script["shots"][:8])
    client = FakeLLMClient([json.dumps(bad), json.dumps(good_script)])
    data, result = write_script(client, series, 1, "outline")
    assert result.ok and data == good_script
    assert len(client.calls) == 2
    assert "failed these checks" in client.calls[1][1]
    assert "words in total" in client.calls[1][1]


def test_fails_clearly_after_second_bad_reply(series):
    client = FakeLLMClient(["not json", '{"title": "x"}'])
    with pytest.raises(PipelineError, match="failed validation twice"):
        write_script(client, series, 1, "outline")


def test_revise_sends_current_script_and_notes(series, good_script):
    client = FakeLLMClient([json.dumps(good_script)])
    write_script(client, series, 1, "outline", current=good_script, notes="Rewrite in second person.")
    prompt = client.calls[0][1]
    assert "Rewrite in second person." in prompt
    assert "Your boat is gone." in prompt


# --- episode files and approval --------------------------------------------------

def test_versions_and_approval(series, good_script):
    ep = Episode.of(series, 1)
    assert ep.save_script(good_script) is None
    assert ep.approval_status() == "draft"

    ep.approve()
    assert ep.is_approved()

    good_script["title"] = "Day One, Revised"
    backup = ep.save_script(good_script)
    assert backup.name == "script.v1.json"
    assert ep.approval_status() == "changed"
    with pytest.raises(PipelineError, match="not approved"):
        ep.require_approved()


# --- command line, end to end with the fake client --------------------------------

def test_cli_writes_script_then_approves(project, capsys):
    base = ["--series", "test-series", "--episode", "1", "--fake-llm"]
    assert make_episode.run(base + ["--outline", "Boat sinks."], root=project) == 0
    out = capsys.readouterr().out
    assert "Day One  [DRAFT]" in out

    ep_dir = project / "series" / "test-series" / "episodes" / "ep01"
    assert json.loads((ep_dir / "script.json").read_text())["title"] == "Day One"
    assert (ep_dir / "outline.txt").read_text().strip() == "Boat sinks."

    assert make_episode.run(base + ["--approve"], root=project) == 0
    assert "[APPROVED]" in capsys.readouterr().out


def test_cli_needs_key_without_fake(project):
    with pytest.raises(PipelineError, match="ANTHROPIC_API_KEY is not set"):
        make_episode.run(["--series", "test-series", "--episode", "1", "--outline", "x"], root=project)


def test_cli_real_mode_needs_sources(project, series_dir):
    (project / ".env").write_text("ANTHROPIC_API_KEY=sk-test\nLLM_MODEL=m\n")
    (series_dir / "sources" / "survival-manual.txt").unlink()
    with pytest.raises(PipelineError, match="No source text"):
        make_episode.run(["--series", "test-series", "--episode", "1", "--outline", "x"], root=project)
