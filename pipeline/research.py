"""Research: find authoritative pages for an episode's facts and save verbatim passages to sources/."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .errors import PipelineError
from .llm import LLMClient, ResearchLimits, ResearchResult
from .series import Series


DEFAULT_GUIDANCE = (
    "Prefer official and expert sources (government, academic, professional bodies, published manuals). "
    "Avoid forums, listicles and AI-generated content."
)


def build_system(series: Series) -> str:
    return "\n\n".join([
        f"You research facts for \"{series.config.title}\", a series of short videos. "
        "Your job is to find reliable published sources, not to write the episode.",
        "# Series bible\n" + series.bible,
        "# How to research\n"
        "- Search for the facts the episode needs, then fetch the 2-3 most authoritative pages and read them. "
        f"{series.config.research_guidance or DEFAULT_GUIDANCE}\n"
        "- Only fetch pages that came up in your searches.\n"
        "- Prefer HTML pages to PDFs. A fetched PDF is read in full, every page, however long it is, "
        "so a whole manual or report can cost more than all your other reading combined. "
        "Fetch a PDF only if it is short or no web page states the fact.\n"
        "- Then write a short brief: one line per fact the episode needs, each backed by a direct quote "
        "from a page you fetched. Quote the exact sentences that state the fact.\n"
        "- If the sources disagree, or a fact the outline assumes is wrong or unsupported, say so plainly.",
    ])


def build_prompt(topic: str, episode: int | None) -> str:
    if episode is not None:
        return (
            f"Episode {episode} outline:\n{topic}\n\n"
            f"Find sources for the lesson and facts episode {episode} needs, "
            "using the season arc in the bible."
        )
    return f"Find sources for this topic:\n{topic}"


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "research"


def format_source_file(topic: str, result: ResearchResult, model: str) -> str:
    lines = [
        f"# Research: {topic.splitlines()[0]}",
        "",
        f"Collected {date.today().isoformat()} by research.py ({model}). Every quote below is verbatim from the page linked.",
        "REVIEW BEFORE USE: open the links, check each quote says what it seems to, and delete anything you don't trust.",
    ]
    by_page: dict[tuple[str, str], list] = {}
    for p in result.passages:
        by_page.setdefault((p.url, p.title), []).append(p)
    for (url, title), passages in by_page.items():
        lines += ["", f"## {title}", url, ""]
        for p in passages:
            tags = [t for t in (
                p.pages,
                "search snippet: page not read in full" if p.snippet else "",
                "" if p.verified else "couldn't confirm which page this came from: check the link",
            ) if t]
            suffix = f" ({'; '.join(tags)})" if tags else ""
            lines.append(f"> {p.text}{suffix}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run_research(
    client: LLMClient, series: Series, topic: str, *, episode: int | None, out_path: Path | None, model: str,
) -> tuple[ResearchResult, str]:
    """Research and, unless out_path is None, save the source file. Returns the result and the file text."""
    limits = ResearchLimits(
        max_searches=series.config.research_max_searches,
        max_fetches=series.config.research_max_fetches,
    )
    result = client.research(build_system(series), build_prompt(topic, episode), limits)
    if not result.passages:
        raise PipelineError(
            "Research found no quotable passages, so nothing was saved. "
            "Try a more specific --topic.\n\nClaude's notes:\n" + (result.notes or "(none)")
        )
    text = format_source_file(topic, result, model)
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    return result, text
