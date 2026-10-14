from types import SimpleNamespace as NS

import pytest

import research
from pipeline.errors import PipelineError
from pipeline.llm import FakeLLMClient, Passage, ResearchResult, _collect
from pipeline.research import build_system, format_source_file, run_research
from pipeline.series import load_series


@pytest.fixture
def series(project):
    return load_series("test-series", project, episode=1)


def fetch_result(url, title, text):
    doc = NS(title=title, source=NS(type="text", data=text))
    return NS(type="web_fetch_tool_result", content=NS(type="web_fetch_result", url=url, content=doc))


def cited(text, *citations):
    return NS(type="text", text=text, citations=list(citations))


def char_cite(quote, index):
    return NS(type="char_location", cited_text=quote, document_index=index)


def test_collect_attributes_quotes_by_page_text():
    blocks = [
        fetch_result("https://a.example", "Page A", "Intro. Never drink seawater. More."),
        fetch_result("https://b.example", "Page B", "You can live three days without water."),
        cited("Seawater is dangerous. ", char_cite("Never drink seawater.", 1)),  # wrong index on purpose
        cited("Three days.", char_cite("You can live three days without water.", 1)),
    ]
    result = ResearchResult(passages=[], notes="")
    _collect(blocks, result)
    assert [(p.url, p.text, p.verified) for p in result.passages] == [
        ("https://a.example", "Never drink seawater.", True),
        ("https://b.example", "You can live three days without water.", True),
    ]
    assert result.notes == "Seawater is dangerous. Three days."


def test_collect_marks_unmatched_quotes_and_dedupes():
    blocks = [
        fetch_result("https://a.example", "Page A", "Something else entirely."),
        cited("x", char_cite("A quote not on the page.", 0), char_cite("A quote not on the page.", 0)),
        NS(type="web_fetch_tool_result", content=NS(type="web_fetch_tool_result_error", error_code="url_not_accessible")),
    ]
    result = ResearchResult(passages=[], notes="")
    _collect(blocks, result)
    assert len(result.passages) == 1
    assert result.passages[0].url == "https://a.example" and not result.passages[0].verified
    assert result.failed_urls == ["(fetch failed: url_not_accessible)"]


def test_source_file_groups_by_page_and_flags_unverified():
    result = ResearchResult(passages=[
        Passage(url="https://a.example", title="Page A", text="Quote one."),
        Passage(url="https://a.example", title="Page A", text="Quote two.", pages="p. 4", verified=False),
    ], notes="")
    text = format_source_file("Topic line\nmore", result, "some-model")
    assert text.startswith("# Research: Topic line\n")
    assert text.count("## Page A") == 1
    assert "> Quote one.\n" in text
    assert "> Quote two. (p. 4; not matched to page text: check the link)" in text
    assert "REVIEW BEFORE USE" in text


def test_guidance_comes_from_series_config(project, series_dir):
    assert "Prefer official and expert sources" in build_system(load_series("test-series", project, 1))
    yaml_path = series_dir / "series.yaml"
    yaml_path.write_text(yaml_path.read_text() + "research_guidance: Use only cookbook sources.\n")
    assert "Use only cookbook sources." in build_system(load_series("test-series", project, 1))


def test_no_passages_saves_nothing(series, tmp_path):
    class Empty(FakeLLMClient):
        def research(self, system, prompt, limits):
            return ResearchResult(passages=[], notes="found nothing useful")

    out = tmp_path / "out.md"
    with pytest.raises(PipelineError, match="found nothing useful"):
        run_research(Empty(), series, "topic", episode=None, out_path=out, model="m")
    assert not out.exists()


def test_cli_saves_to_sources_and_refuses_overwrite(project, series_dir, capsys):
    args = ["--series", "test-series", "--episode", "1", "--outline", "Boat sinks."]
    assert research.run(args, root=project, client=FakeLLMClient()) == 0
    saved = series_dir / "sources" / "ep01-research.md"
    assert "FAKE: Never drink seawater" in saved.read_text()
    assert (series_dir / "episodes" / "ep01" / "outline.txt").read_text().strip() == "Boat sinks."

    with pytest.raises(PipelineError, match="already exists"):
        research.run(args, root=project, client=FakeLLMClient())


def test_cli_fake_llm_never_writes_sources(project, series_dir):
    assert research.run(["--series", "test-series", "--topic", "rain", "--fake-llm"], root=project) == 0
    assert sorted(p.name for p in (series_dir / "sources").iterdir()) == ["survival-manual.txt"]
