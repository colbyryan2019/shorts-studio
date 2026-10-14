from types import SimpleNamespace as NS

import pytest

import research
from pipeline.errors import PipelineError
from pipeline.llm import AnthropicClient, FakeLLMClient, Passage, ResearchLimits, ResearchResult, _collect
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
    assert "> Quote two. (p. 4; couldn't confirm which page this came from: check the link)" in text
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


def pdf_result(url):
    doc = NS(title=None, source=NS(type="base64", data="JVBERi0..."))
    return NS(type="web_fetch_tool_result", content=NS(type="web_fetch_result", url=url, content=doc))


def page_cite(quote, index, start, end):
    return NS(type="page_location", cited_text=quote, document_index=index,
              start_page_number=start, end_page_number=end)


def search_cite(quote, url):
    return NS(type="web_search_result_location", cited_text=quote, url=url, title="T")


def test_single_pdf_quotes_are_attributed_with_page_numbers():
    blocks = [
        fetch_result("https://a.example", "Page A", "Some text."),
        pdf_result("https://manual.example/fm.pdf"),
        cited("x", page_cite("Do not drink seawater.", 0, 176, 177), page_cite("Rest.", 1, 16, 18)),
    ]
    result = ResearchResult(passages=[], notes="")
    _collect(blocks, result)
    assert [(p.url, p.pages, p.verified) for p in result.passages] == [
        ("https://manual.example/fm.pdf", "PDF page 176", True),
        ("https://manual.example/fm.pdf", "PDF page 16-17", True),
    ]


def test_two_pdfs_fall_back_to_index_unverified():
    blocks = [pdf_result("https://one.example/a.pdf"), pdf_result("https://two.example/b.pdf"),
              cited("x", page_cite("Quote.", 1, 3, 4))]
    result = ResearchResult(passages=[], notes="")
    _collect(blocks, result)
    assert (result.passages[0].url, result.passages[0].verified) == ("https://two.example/b.pdf", False)


def test_search_snippets_kept_and_marked_unless_cut_off():
    blocks = [cited("x", search_cite("Kidneys can't make urine saltier than seawater.", "https://noaa.example"),
                    search_cite("humans can stay alive 3 minutes with...", "https://news.example"))]
    result = ResearchResult(passages=[], notes="")
    _collect(blocks, result)
    assert [(p.url, p.snippet) for p in result.passages] == [("https://noaa.example", True)]


def test_pdfs_are_recorded_so_the_cli_can_warn():
    result = ResearchResult(passages=[], notes="")
    _collect([fetch_result("https://a.example", "A", "text"), pdf_result("https://m.example/fm.pdf")], result)
    assert result.pdf_urls == ["https://m.example/fm.pdf"]


def test_research_requests_use_caching_and_count_cache_reads():
    usage = NS(input_tokens=1000, cache_read_input_tokens=90000, cache_creation_input_tokens=5000,
               output_tokens=200, server_tool_use=NS(web_search_requests=2, web_fetch_requests=1))
    response = NS(content=[cited("brief")], usage=usage, stop_reason="end_turn")
    calls = []
    client = AnthropicClient(api_key="test", model="m")
    client._create = lambda **kw: calls.append(kw) or response
    result = client.research("sys", "prompt", ResearchLimits())
    assert calls[0]["cache_control"] == {"type": "ephemeral"}
    assert (result.input_tokens, result.cached_input_tokens) == (96000, 90000)
