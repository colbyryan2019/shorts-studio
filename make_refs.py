"""Reference images, so characters and objects look the same in every shot.

    python make_refs.py --series stranded-at-sea                 # 1. propose refs/refs.yaml from the bible (or show status)
    python make_refs.py --series stranded-at-sea --generate      # 2. draw candidates into refs/candidates/
    python make_refs.py --series stranded-at-sea --pick survivor 2   # 3. use candidate 2 as refs/survivor.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline import refs
from pipeline.config import PROJECT_ROOT
from pipeline.errors import PipelineError
from pipeline.imagegen import ROUGH_COST_PER_IMAGE, ImageClient, make_budget, make_image_client
from pipeline.llm import FAKE_REFS, FakeLLMClient, LLMClient, make_client
from pipeline.series import load_series


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plan, generate and pick a series' reference images.")
    p.add_argument("--series", required=True, help="folder name under series/")
    p.add_argument("--propose", action="store_true", help="(re)write refs.yaml from the bible; needs --force if it exists")
    p.add_argument("--generate", nargs="*", metavar="NAME", help="draw candidates for these refs (default: all)")
    p.add_argument("--from", dest="base", type=int, metavar="N",
                   help="with --generate NAME: start the new candidates from candidate N (adds new numbers)")
    p.add_argument("--style", help="with --generate: try this art style instead of style_line.txt (adds new numbers)")
    p.add_argument("--count", type=int, help="with --generate: how many candidates per ref (default: ref_candidates)")
    p.add_argument("--pick", nargs=2, metavar=("NAME", "N"), help="use candidate N as the reference for NAME")
    p.add_argument("--force", action="store_true", help="overwrite refs.yaml, or redraw existing candidates")
    p.add_argument("--dry-run", action="store_true", help="with --generate: list what would be drawn, spend nothing")
    p.add_argument("--fake-llm", action="store_true", help="propose canned refs instead of calling Claude")
    p.add_argument("--fake-images", action="store_true", help="draw placeholders instead of calling the image API")
    args = p.parse_args(argv)
    if sum([args.propose, args.generate is not None, args.pick is not None]) > 1:
        p.error("use one of --propose, --generate or --pick at a time")
    if (args.style or args.count) and args.generate is None:
        p.error("--style and --count go with --generate")
    if args.count is not None and not 1 <= args.count <= 6:
        p.error("--count must be 1-6")
    if args.base is not None and args.generate is None:
        p.error("--from goes with --generate NAME, e.g. --generate survivor --from 2")
    if args.pick and not args.pick[1].isdigit():
        p.error("--pick takes a name and a candidate number, e.g. --pick survivor 2")
    return args


def run(argv: list[str] | None = None, root: Path = PROJECT_ROOT,
        llm: LLMClient | None = None, images: ImageClient | None = None) -> int:
    args = parse_args(argv)
    series = load_series(args.series, root, episode=1)
    refs_file = series.refs_dir / refs.REFS_FILE

    if args.pick:
        dest = refs.pick(series, args.pick[0], int(args.pick[1]))
        print(f"Saved {dest}. Shots that show the {args.pick[0]} will now use it.")
        print("Scripts written before this need the ref added: check them with make_episode.py.")
        return 0

    if args.generate is not None:
        if not series.ref_specs:
            raise PipelineError(f"No {refs_file}. Run without --generate first to propose it.")
        names = args.generate or list(series.ref_specs)
        unknown = [n for n in names if n not in series.ref_specs]
        if unknown:
            raise PipelineError(f"Not in refs.yaml: {', '.join(unknown)}. Known: {', '.join(series.ref_specs)}")
        todo = refs.planned_candidates(series, names, args.force,
                                       refine=args.base is not None or args.style is not None, count=args.count)
        rough = ROUGH_COST_PER_IMAGE.get(series.config.image_quality)
        estimate = f", roughly ${rough * len(todo):.2f}" if rough is not None and todo else ""
        print(f"{len(todo)} candidates to draw{estimate}.")
        if args.dry_run or not todo:
            for name, i in todo:
                print(f"  {refs.candidate_path(series, name, i).name}")
            return 0
        client = images or make_image_client(args.fake_images, root, series.config.image_quality)
        budget = make_budget(client.model, root)
        refs.generate_candidates(client, series, names, budget, force=args.force, base=args.base,
                                 style=args.style, count=args.count)
        print(f"Cost this run: ${budget.spent:.2f}")
        print(f"\nLook at the images in {series.refs_dir / refs.CANDIDATES_DIR}, then pick one per ref, e.g.:")
        print(f"  python make_refs.py --series {series.name} --pick {names[0]} 1")
        return 0

    if args.propose or not refs_file.exists():
        if refs_file.exists() and not args.force:
            raise PipelineError(f"{refs_file} already exists. Edit it by hand, or pass --force to replace it.")
        if llm is None:
            llm = FakeLLMClient([FAKE_REFS]) if args.fake_llm else make_client(False, root)[0]
        print("Asking Claude which recurring characters and objects need reference images...")
        path = refs.save_ref_specs(series, refs.propose_refs(llm, series))
        series = load_series(args.series, root, episode=1)
        print(f"Saved {path}. Edit it if anything is off, then run with --generate.\n")

    print(f"References for {series.config.title}:")
    print("\n".join(refs.status_lines(series)))
    return 0


def main() -> int:
    try:
        return run()
    except PipelineError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
