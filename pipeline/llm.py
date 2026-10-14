"""LLM clients behind one small interface, so the script step can run without a network."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from pathlib import Path

import anthropic

from .config import load_settings
from .errors import PipelineError


@dataclass
class Passage:
    """A verbatim passage quoted from a web page, with where it came from."""

    url: str
    title: str
    text: str
    pages: str = ""  # e.g. "PDF page 12-13": pages of the PDF file, not the printed page numbers
    verified: bool = True  # False when we can't be sure which fetched page the quote came from
    snippet: bool = False  # True for search-result snippets (the page itself wasn't read)


@dataclass
class ResearchResult:
    passages: list[Passage]
    notes: str  # Claude's own summary; shown to the user, never saved as a source
    searches: int = 0
    fetches: int = 0
    input_tokens: int = 0  # all input, including cache reads
    cached_input_tokens: int = 0  # the part read from cache, billed at about a tenth of the normal rate
    output_tokens: int = 0
    failed_urls: list[str] = field(default_factory=list)
    pdf_urls: list[str] = field(default_factory=list)  # PDFs are read in full; max_content_tokens doesn't cap them


@dataclass
class ResearchLimits:
    max_searches: int = 5
    max_fetches: int = 3
    max_content_tokens: int = 10000  # per fetched text page; the API doesn't apply it to PDFs


class LLMClient(Protocol):
    def complete(self, system: str, prompt: str) -> str:
        """Send one system prompt + one user message, return the reply text."""
        ...

    def research(self, system: str, prompt: str, limits: ResearchLimits) -> ResearchResult:
        """Search and read the web, return the passages Claude quoted."""
        ...


class AnthropicClient:
    def __init__(self, api_key: str, model: str, max_tokens: int = 16000):
        # Key passed explicitly so the SDK never falls back to shell variables or profiles.
        # max_retries gives exponential backoff on 429 / 5xx / connection errors.
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=4)
        self.model = model
        self.max_tokens = max_tokens

    def _create(self, **kwargs):
        try:
            response = self._client.messages.create(model=self.model, max_tokens=self.max_tokens, **kwargs)
        except anthropic.AuthenticationError as e:
            raise PipelineError("Anthropic rejected ANTHROPIC_API_KEY. Check the key in .env.") from e
        except anthropic.NotFoundError as e:
            raise PipelineError(f"Model '{self.model}' not found. Check LLM_MODEL in .env.") from e
        except anthropic.BadRequestError as e:
            raise PipelineError(f"Anthropic API rejected the request: {e.message}") from e
        except anthropic.APIStatusError as e:
            raise PipelineError(f"Anthropic API error {e.status_code} (after retries): {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise PipelineError("Could not reach the Anthropic API. Check your internet connection.") from e

        if response.stop_reason == "refusal":
            raise PipelineError("The model declined this request. Try rewording the outline or topic.")
        if response.stop_reason == "max_tokens":
            raise PipelineError(f"The reply was cut off at max_tokens={self.max_tokens}.")
        return response

    def complete(self, system: str, prompt: str) -> str:
        response = self._create(system=system, messages=[{"role": "user", "content": prompt}])
        return "".join(block.text for block in response.content if block.type == "text")

    def research(self, system: str, prompt: str, limits: ResearchLimits) -> ResearchResult:
        # Basic tool versions (no dynamic filtering): fetched pages come back as plain documents,
        # so the passages Claude cites are exact text from the page we can check and save.
        tools = [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": limits.max_searches},
            {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": limits.max_fetches,
             "citations": {"enabled": True}, "max_content_tokens": limits.max_content_tokens},
        ]
        user = {"role": "user", "content": prompt}
        messages = [user]
        blocks = []
        result = ResearchResult(passages=[], notes="")
        for _ in range(MAX_CONTINUATIONS):
            # The server re-reads the whole conversation (every fetched page) after each search or fetch.
            # Caching makes those re-reads cost about a tenth; the server adds cache points after tool results.
            response = self._create(system=system, messages=messages, tools=tools,
                                    cache_control={"type": "ephemeral"})
            blocks.extend(response.content)
            usage = response.usage
            cached = usage.cache_read_input_tokens or 0
            result.input_tokens += usage.input_tokens + cached + (usage.cache_creation_input_tokens or 0)
            result.cached_input_tokens += cached
            result.output_tokens += usage.output_tokens
            if usage.server_tool_use:
                result.searches += usage.server_tool_use.web_search_requests or 0
                result.fetches += usage.server_tool_use.web_fetch_requests or 0
            if response.stop_reason != "pause_turn":
                break
            # The server paused a long search turn: send it back unchanged to let it continue.
            messages = [user, {"role": "assistant", "content": response.content}]
        _collect(blocks, result)
        return result


def make_client(fake: bool, root: Path) -> tuple[LLMClient, str]:
    """The fake client, or the real one after checking .env. Returns (client, model name)."""
    if fake:
        return FakeLLMClient(), "fake-llm"
    settings = load_settings(root)
    if not settings.anthropic_api_key:
        raise PipelineError(
            f"ANTHROPIC_API_KEY is not set. Add it to {root / '.env'} "
            "(or run with --fake-llm to test without a key)."
        )
    if not settings.llm_model:
        raise PipelineError(
            f"LLM_MODEL is not set. Add a Claude model id to {root / '.env'}, e.g. LLM_MODEL=claude-sonnet-5-5"
        )
    return AnthropicClient(api_key=settings.anthropic_api_key, model=settings.llm_model), settings.llm_model


MAX_CONTINUATIONS = 5


def _collect(blocks: list, result: ResearchResult) -> None:
    """Turn response blocks into verbatim passages, each attributed to the page it was found in."""
    docs = []  # (url, title, text or None for PDFs) in fetch order
    for b in blocks:
        if b.type == "web_fetch_tool_result":
            if b.content.type == "web_fetch_result":
                doc = b.content.content
                text = doc.source.data if getattr(doc.source, "type", "") == "text" else None
                docs.append((b.content.url, doc.title or b.content.url, text))
                if text is None:
                    result.pdf_urls.append(b.content.url)
            else:
                result.failed_urls.append(f"(fetch failed: {b.content.error_code})")

    notes, seen = [], set()
    for b in blocks:
        if b.type != "text":
            continue
        notes.append(b.text)
        for c in b.citations or []:
            text = " ".join(c.cited_text.split())
            if not text or text in seen:
                continue
            seen.add(text)
            if c.type == "web_search_result_location":
                if text.endswith(("...", "\u2026")):
                    continue  # search snippets are capped at ~150 characters; a cut-off quote is useless
                result.passages.append(Passage(url=c.url, title=c.title or c.url, text=text, snippet=True))
            elif c.type == "char_location":
                url, title, verified = _find_text_document(docs, c.document_index, c.cited_text)
                result.passages.append(Passage(url=url, title=title, text=text, verified=verified))
            elif c.type == "page_location":
                last = c.end_page_number - 1  # end is exclusive
                pages = f"PDF page {c.start_page_number}" + (f"-{last}" if last > c.start_page_number else "")
                url, title, verified = _find_pdf_document(docs, c.document_index)
                result.passages.append(Passage(url=url, title=title, text=text, pages=pages, verified=verified))
    result.notes = "".join(notes).strip()


def _find_text_document(docs: list, index: int, cited: str) -> tuple[str, str, bool]:
    """The fetched text page that contains the quote; else fall back to the citation's index, unverified."""
    for url, title, text in docs:
        if text and cited.strip() and cited.strip() in text:
            return url, title, True
    return _by_index(docs, index)


def _find_pdf_document(docs: list, index: int) -> tuple[str, str, bool]:
    """PDF text isn't returned as plain text, so a page citation is certain only if one PDF was read."""
    pdfs = [(url, title) for url, title, text in docs if text is None]
    if len(pdfs) == 1:
        return pdfs[0][0], pdfs[0][1], True
    return _by_index(docs, index)


def _by_index(docs: list, index: int) -> tuple[str, str, bool]:
    if 0 <= index < len(docs):
        return docs[index][0], docs[index][1], False
    return "(unknown page)", "Unknown source", False


class FakeLLMClient:
    """Returns canned replies. With no replies given, always returns FAKE_SCRIPT.

    Records every call in .calls so tests can inspect the prompts.
    """

    def __init__(self, replies: list[str] | None = None):
        self._replies = list(replies) if replies is not None else None
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        if self._replies is None:
            return json.dumps(FAKE_SCRIPT, ensure_ascii=False)
        if not self._replies:
            raise AssertionError("FakeLLMClient ran out of replies")
        return self._replies.pop(0)

    def research(self, system: str, prompt: str, limits: ResearchLimits) -> ResearchResult:
        self.calls.append((system, prompt))
        return ResearchResult(
            passages=[Passage(**p) for p in FAKE_PASSAGES],
            notes="(fake research: canned passages, not real sources)",
            searches=0, fetches=0,
        )


# A valid episode 1 for the default series limits (8-12 shots, 90-110 words, <=2 genvideo).
# refs are empty because no reference images exist yet.
FAKE_SCRIPT = {
    "title": "Day One",
    "end_card": "DAY 1 · WATER: NONE",
    "shots": [
        {"n": 1, "narration": "Your boat is gone.",
         "image_prompt": "A sailboat mast sinking under dark water, a small dinghy nearby", "motion": "drift", "refs": []},
        {"n": 2, "narration": "No land. No radio. No one is coming.",
         "image_prompt": "A tiny dinghy alone on a flat empty ocean, wide shot", "motion": "rock", "refs": []},
        {"n": 3, "narration": "One tarp. One knife. One mirror. No water.",
         "image_prompt": "A tarp, a knife and a small mirror laid out on the dinghy floor", "motion": "pushin", "refs": []},
        {"n": 4, "narration": "Without water, you have three days. Maybe less.",
         "image_prompt": "The stick-figure survivor in a wide-brim hat sitting still in the dinghy", "motion": "rock", "refs": []},
        {"n": 5, "narration": "The ocean is right there. Every instinct tells you to drink.",
         "image_prompt": "The survivor's hand hovering just above the water's surface", "motion": "genvideo", "refs": []},
        {"n": 6, "narration": "Don't. Seawater is saltier than your kidneys can handle.",
         "image_prompt": "The survivor pulling the hand back from the water", "motion": "pushin", "refs": []},
        {"n": 7, "narration": "To flush the salt, your body burns water it doesn't have. Every sip brings you closer to the end.",
         "image_prompt": "A simple outline of a body with the water inside it draining away", "motion": "drift", "refs": []},
        {"n": 8, "narration": "You let it go.",
         "image_prompt": "The survivor sitting back in the dinghy, looking away from the water", "motion": "rock", "refs": []},
        {"n": 9, "narration": "The sun climbs. Every drop of sweat is water you'll never get back.",
         "image_prompt": "A harsh sun high above the dinghy, no shade anywhere", "motion": "pushin", "refs": []},
        {"n": 10, "narration": "And on the horizon, a wall of black cloud is moving toward you.",
         "image_prompt": "A dark storm front rising on the horizon behind the small dinghy", "motion": "drift", "refs": []},
    ],
    "claims": [
        {"claim": "Drinking seawater dehydrates you because your kidneys need extra water to flush the salt",
         "source_file": "survival-manual.txt"},
        {"claim": "Without drinking water, a person may survive only about three days",
         "source_file": "survival-manual.txt"},
        {"claim": "Sweating loses body water; staying still and shaded reduces it",
         "source_file": "survival-manual.txt"},
    ],
}


# Placeholder passages for --fake-llm. Clearly marked so they are never mistaken for real sources.
FAKE_PASSAGES = [
    {"url": "https://example.invalid/fake-survival-manual", "title": "FAKE SOURCE (from --fake-llm)",
     "text": "FAKE: Never drink seawater. The salt makes your kidneys use more water than you drank."},
    {"url": "https://example.invalid/fake-survival-manual", "title": "FAKE SOURCE (from --fake-llm)",
     "text": "FAKE: Without water a person may survive about three days. Shade and stillness reduce sweating."},
]

# Canned refs.yaml proposal for --fake-llm in make_refs.py.
FAKE_REFS = json.dumps({"refs": [
    {"name": "survivor",
     "description": "A faceless stick figure in a wide-brim hat, drawn with simple black lines.",
     "keywords": ["survivor", "stick figure", "figure"]},
    {"name": "dinghy",
     "description": "A small rounded inflatable dinghy, plain outline, flat muted grey.",
     "keywords": ["dinghy", "boat", "raft"]},
]})
