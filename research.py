"""Find sources for an episode (or any topic) on the web and save verbatim passages to the series' sources/.

    python research.py --series stranded-at-sea --episode 1            # uses the episode's saved outline
    python research.py --series stranded-at-sea --topic "how to catch rainwater"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline.config import PROJECT_ROOT
from pipeline.episode import Episode
from pipeline.errors import PipelineError
from pipeline.llm import LLMClient, make_client
from pipeline.research import run_research, slugify
from pipeline.series import episode_dir_name, load_series


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Research sources for a series and save them to sources/.")
    p.add_argument("--series", required=True, help="folder name under series/")
    p.add_argument("--episode", type=int, help="research this episode (uses its outline)")
    p.add_argument("--outline", help="episode outline; saved for make_episode.py too (with --episode)")
    p.add_argument("--topic", help="research a free-text topic instead of an episode")
    p.add_argument("--name", help="output file name in sources/ (default: epNN-research or the topic)")
    p.add_argument("--force", action="store_true", help="overwrite an existing file with the same name")
    p.add_argument("--fake-llm", action="store_true", help="save canned placeholder passages, no API call")
    args = p.parse_args(argv)
    if (args.episode is None) == (args.topic is None):
        p.error("give either --episode N or --topic \"...\"")
    if args.outline and args.episode is None:
        p.error("--outline needs --episode")
    if args.episode is not None and args.episode < 1:
        p.error("--episode must be 1 or more")
    return args


def run(argv: list[str] | None = None, root: Path = PROJECT_ROOT, client: LLMClient | None = None) -> int:
    args = parse_args(argv)
    series = load_series(args.series, root, args.episode or 1)

    if args.episode is not None:
        ep = Episode.of(series, args.episode)
        if args.outline:
            ep.save_outline(args.outline)
        topic = ep.load_outline()
        if not topic:
            raise PipelineError(f"No outline saved for {ep.path.name}. Pass --outline \"...\" or use --topic.")
        default_name = f"{episode_dir_name(args.episode)}-research"
    else:
        topic = args.topic.strip()
        default_name = slugify(topic)

    name = args.name or default_name
    out_path = series.path / "sources" / (name if name.endswith((".md", ".txt")) else f"{name}.md")
    if out_path.exists() and not args.force:
        raise PipelineError(f"{out_path} already exists. Pass --force to overwrite it, or --name to save elsewhere.")

    if client is None:
        llm, model = make_client(args.fake_llm, root)
    else:
        llm, model = client, "test"
    limits = series.config
    print(f"Researching (up to {limits.research_max_searches} searches, {limits.research_max_fetches} page reads)...")
    # Fake passages must never land in sources/, where a real script run would treat them as facts.
    save_to = None if args.fake_llm else out_path
    result, text = run_research(llm, series, topic, episode=args.episode, out_path=save_to, model=model)
    if save_to is None:
        print(f"\n--fake-llm: nothing saved. This is what {out_path.name} would contain:\n")
        print(text)
        return 0

    pages = {p.url for p in result.passages}
    unverified = sum(1 for p in result.passages if not p.verified)
    print(f"\nSaved {len(result.passages)} passages from {len(pages)} pages to {out_path}")
    if unverified:
        print(f"  ! {unverified} passages couldn't be tied to a specific page for certain; they're marked in the file.")
    if result.failed_urls:
        print(f"  ! {len(result.failed_urls)} page fetches failed.")
    for url in result.pdf_urls:
        print(f"  ! Read a full PDF (no length cap applies, so it drives cost): {url}")
    print(f"Usage: {result.searches} searches, {result.fetches} page reads, "
          f"{result.input_tokens:,} input tokens ({result.cached_input_tokens:,} from cache), "
          f"{result.output_tokens:,} output tokens")
    print("\nClaude's notes (not saved as a source):\n")
    print(result.notes or "(none)")
    print(f"\nNext: open {out_path.name}, check the quotes and links, delete anything you don't trust. "
          "Then write the script with make_episode.py.")
    return 0


def main() -> int:
    try:
        return run()
    except PipelineError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
