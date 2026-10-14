import json

import pytest

from pipeline.config import load_settings
from pipeline.errors import PipelineError
from pipeline.series import load_series


def test_loads_everything(project):
    s = load_series("test-series", project, episode=1)
    assert s.config.title == "Test Series"
    assert s.config.words == (90, 110)
    assert "Second person only" in s.house_rules
    assert "lost at sea" in s.bible
    assert s.style_line == "Stick figures."
    assert s.sources == {"survival-manual.txt": "Never drink seawater."}
    assert s.refs == ["dinghy"]
    assert s.recent_scripts == {}


def test_ignores_hidden_and_unsupported_files(project, series_dir):
    (series_dir / "sources" / ".gitkeep").write_text("")
    (series_dir / "sources" / "notes.pdf").write_bytes(b"pdf")
    (series_dir / "refs" / ".gitkeep").write_text("")
    s = load_series("test-series", project, episode=1)
    assert list(s.sources) == ["survival-manual.txt"]
    assert s.refs == ["dinghy"]


def test_unknown_series_lists_available(project):
    with pytest.raises(PipelineError, match="Available: test-series"):
        load_series("nope", project, episode=1)


@pytest.mark.parametrize("name, what", [
    ("bible.md", "series bible"),
    ("style_line.txt", "style line"),
    ("series.yaml", "series config"),
])
def test_missing_required_file(project, series_dir, name, what):
    (series_dir / name).unlink()
    with pytest.raises(PipelineError, match=f"Missing {what}"):
        load_series("test-series", project, episode=1)


def test_missing_house_rules(project):
    (project / "house_rules.md").unlink()
    with pytest.raises(PipelineError, match="Missing house rules"):
        load_series("test-series", project, episode=1)


@pytest.mark.parametrize("bad, field", [
    ("words: [110, 90]", "words"),
    ("default_motion: spin", "default_motion"),
    ("max_genvideo_shots: -1", "max_genvideo_shots"),
    ("wrods: [1, 2]", "wrods"),
])
def test_invalid_yaml_values(project, series_dir, bad, field):
    path = series_dir / "series.yaml"
    path.write_text(path.read_text() + bad + "\n")
    with pytest.raises(PipelineError, match=field):
        load_series("test-series", project, episode=1)


def test_recent_scripts_are_last_three_before_episode(project, series_dir):
    for n in (1, 2, 3, 4, 6):
        d = series_dir / "episodes" / f"ep{n:02d}"
        d.mkdir(parents=True)
        (d / "script.json").write_text(json.dumps({"title": f"ep {n}"}))
    s = load_series("test-series", project, episode=5)
    assert list(s.recent_scripts) == [2, 3, 4]
    assert s.recent_scripts[4]["title"] == "ep 4"


def test_settings_come_only_from_dotenv(project, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    assert load_settings(project).anthropic_api_key == ""
    (project / ".env").write_text("ANTHROPIC_API_KEY=sk-test\nLLM_MODEL=some-model\n")
    settings = load_settings(project)
    assert settings.anthropic_api_key == "sk-test"
    assert settings.llm_model == "some-model"
    assert "sk-test" not in repr(settings)
