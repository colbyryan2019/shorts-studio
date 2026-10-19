"""Start a new series: copy templates/series/ into series/<name>/ with a starter bible, style line and series.yaml.

    python new_series.py --name next-idea
    python new_series.py --name next-idea --title "The Next Idea"
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from pipeline.config import PROJECT_ROOT
from pipeline.errors import PipelineError
from pipeline.series import REF_NAME_RE as NAME_RE  # same rule as ref names: lowercase, digits, dashes
SKIP = {".gitkeep", ".DS_Store"}
FOLDERS = ["sources", "refs", "episodes"]
TEXT_SUFFIXES = {".md", ".txt", ".yaml"}


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create a new series folder from the templates.")
    p.add_argument("--name", required=True, help="folder name under series/: lowercase letters, digits and dashes")
    p.add_argument("--title", help="display title (default: from the name, e.g. next-idea -> Next Idea)")
    args = p.parse_args(argv)
    if not NAME_RE.match(args.name):
        p.error(f"--name '{args.name}' must be lowercase letters, digits and dashes, e.g. lost-in-the-desert")
    if args.title is not None and not args.title.strip():
        p.error("--title can't be empty")
    return args


def default_title(name: str) -> str:
    return " ".join(word.capitalize() for word in name.split("-") if word)


def create_series(name: str, title: str, root: Path, templates: Path) -> Path:
    if not templates.is_dir():
        raise PipelineError(f"Templates not found: {templates}")
    dest = root / "series" / name
    if dest.exists():
        raise PipelineError(f"{dest} already exists. Pick another --name, or edit that series directly.")
    for src in sorted(templates.rglob("*")):
        rel = src.relative_to(templates)
        if src.name in SKIP or not src.is_file():
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix in TEXT_SUFFIXES:
            text = src.read_text(encoding="utf-8").replace("__TITLE__", title).replace("__NAME__", name)
            out.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(src, out)
    for folder in FOLDERS:
        (dest / folder).mkdir(parents=True, exist_ok=True)
    return dest


def run(argv: list[str] | None = None, root: Path = PROJECT_ROOT,
        templates: Path = PROJECT_ROOT / "templates" / "series") -> int:
    args = parse_args(argv)
    title = (args.title or default_title(args.name)).strip()
    if '"' in title or "\n" in title:
        raise PipelineError("--title can't contain double quotes or line breaks")
    dest = create_series(args.name, title, root, templates)
    print(f"Created {dest} (\"{title}\"). Next:")
    print("  1. Fill in every TODO line in bible.md and style_line.txt.")
    print("  2. Check series.yaml: voice_id, words/shots, end_card_format, sources_required, research_guidance.")
    print(f"  3. Refs:     python make_refs.py --series {args.name}   (then --generate and --pick)")
    print(f"  4. Sources:  python research.py --series {args.name} --episode 1 --outline \"...\"")
    print(f"  5. Script:   python make_episode.py --series {args.name} --episode 1 --outline \"...\"")
    print("series/ is git-ignored, so none of it is committed.")
    return 0


def main() -> int:
    try:
        return run()
    except PipelineError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
