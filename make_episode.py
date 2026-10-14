"""Turn an episode outline into a vertical video. Milestone 1: the script step, with review and approval.

    python make_episode.py --series stranded-at-sea --episode 1 --outline "..." [--fake-llm]
    python make_episode.py --series stranded-at-sea --episode 1 --revise "make line 4 punchier"
    python make_episode.py --series stranded-at-sea --episode 1 --approve
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline.config import PROJECT_ROOT
from pipeline.episode import Episode
from pipeline.errors import PipelineError
from pipeline.llm import LLMClient, make_client
from pipeline.schema import validate_script
from pipeline.script import format_summary, planned_calls, write_script
from pipeline.series import Series, load_series


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Make one episode of a series.")
    p.add_argument("--series", required=True, help="folder name under series/")
    p.add_argument("--episode", required=True, type=int, help="episode number, e.g. 1")
    p.add_argument("--outline", help="short outline of the episode (needed the first time)")
    p.add_argument("--dry-run", action="store_true", help="write the script, list planned paid calls, spend nothing else")
    p.add_argument("--redo-script", action="store_true", help="write a new script from the outline (the old one is kept as script.vN.json)")
    p.add_argument("--revise", metavar="NOTES", help="ask the LLM to revise the current script per these notes")
    p.add_argument("--approve", action="store_true", help="approve the current script so later steps may run")
    p.add_argument("--fake-llm", action="store_true", help="use a canned script instead of calling the Claude API")
    args = p.parse_args(argv)

    if args.episode < 1:
        p.error("--episode must be 1 or more")
    if args.revise is not None and args.redo_script:
        p.error("use either --revise or --redo-script, not both")
    if args.approve and (args.revise is not None or args.redo_script):
        p.error("--approve can't be combined with --revise or --redo-script: review the new script first")
    if args.revise is not None and not args.revise.strip():
        p.error("--revise needs notes, e.g. --revise \"rewrite in second person\"")
    return args


def make_script_client(args: argparse.Namespace, series: Series, root: Path) -> LLMClient:
    if series.config.sources_required and not series.sources:
        if not args.fake_llm:
            raise PipelineError(
                f"No source text in {series.path / 'sources'} and sources_required is true. "
                f"Run: python research.py --series {series.name} --episode {args.episode}  "
                "(or add .txt / .md files yourself), then run again."
            )
        print(f"Warning: {series.path / 'sources'} is empty; continuing because of --fake-llm.\n")
    client, _ = make_client(args.fake_llm, root)
    return client


def run(argv: list[str] | None = None, root: Path = PROJECT_ROOT, client: LLMClient | None = None) -> int:
    args = parse_args(argv)
    series = load_series(args.series, root, args.episode)
    ep = Episode.of(series, args.episode)
    missing_sources_ok = args.fake_llm and not series.sources
    revising = args.revise is not None

    if args.outline:
        if ep.has_script() and not (args.redo_script or revising) and args.outline.strip() != ep.load_outline():
            print("Note: a script already exists, so --outline was not used. Add --redo-script to write a new one.\n")
        else:
            ep.save_outline(args.outline)

    if revising and not ep.has_script():
        raise PipelineError(f"Nothing to revise: {ep.script_path} doesn't exist yet. Run with --outline first.")

    if args.redo_script or revising or not ep.has_script():
        outline = ep.load_outline()
        if not outline:
            raise PipelineError("No outline for this episode. Pass --outline \"...\".")
        current = None
        if revising:
            current = ep.load_script_data()
            if not isinstance(current, dict):
                raise PipelineError(f"{ep.script_path} must contain a JSON object to revise.")
        llm = client or make_script_client(args, series, root)
        print("Revising the script..." if revising else "Writing the script...")
        data, _ = write_script(
            llm, series, args.episode, outline,
            current=current, notes=args.revise, missing_sources_ok=missing_sources_ok,
        )
        backup = ep.save_script(data)
        print(f"Saved {ep.script_path}" + (f" (previous version kept as {backup.name})" if backup else ""))
    else:
        print(f"Using existing {ep.script_path} (edit it by hand, or use --revise / --redo-script)")

    # Always re-check what is on disk: it may have been edited by hand.
    result = validate_script(ep.load_script_data(), series, missing_sources_ok=missing_sources_ok)
    if result.script is None:
        print(f"\n{ep.script_path} does not match the script format:")
        print("\n".join(f"  x {e}" for e in result.errors))
        return 1

    if args.approve:
        if result.errors:
            print(format_summary(result.script, series, ep.approval_status(), result.warnings, result.errors))
            print("\nNot approved: fix the problems above first.")
            return 1
        ep.approve()

    status = ep.approval_status()
    print()
    print(format_summary(result.script, series, status, result.warnings, result.errors))
    print()

    if status != "approved":
        print("Next: review the script, then either edit script.json, run with --revise \"notes\", or --approve.")
        if args.dry_run:
            print()
            print(planned_calls(result.script))
            print("(These would wait until the script is approved.)")
        return 1 if result.errors else 0

    if args.dry_run:
        print(planned_calls(result.script))
    else:
        print("Script approved. Images, voice and video arrive in later milestones; nothing else to do yet.")
    return 0


def main() -> int:
    try:
        return run()
    except PipelineError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
