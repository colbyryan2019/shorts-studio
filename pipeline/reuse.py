"""Reuse a cached file that moved to another shot number.

Images and narration are cached per file (01.png, 01.mp3) with a fingerprint of their inputs.
Inserting, removing or reordering shots renumbers them, so without this every later shot
would be paid for again although an identical file already exists under its old number.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable


def find_moved(manifest: dict, folder: Path, name: str, fingerprint: str,
               usable: Callable[[dict], bool] = lambda entry: True) -> str:
    """Another file in the manifest made from exactly the same inputs, or ""."""
    for other, entry in sorted(manifest.items()):
        if (other != name and entry.get("fingerprint") == fingerprint and usable(entry)
                and (folder / other).is_file()):
            return other
    return ""


def apply_moves(folder: Path, manifest_name: str, plans: list) -> int:
    """Copy each plan's `copy_from` file (and its manifest entry) to its new number, and mark it done.

    Everything is read before anything is written, since a source may itself be the target of
    another move. Returns how many files were copied.
    """
    moves = [p for p in plans if p.copy_from]
    if not moves:
        return 0
    manifest_path = folder / manifest_name
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    staged = [(p, (folder / p.copy_from).read_bytes(), dict(manifest[p.copy_from])) for p in moves]
    for p, data, entry in staged:
        p.path.write_bytes(data)
        manifest[p.path.name] = entry
        p.copy_from, p.reason = "", ""
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return len(staged)
