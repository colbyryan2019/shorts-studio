"""Files in one episode folder: script.json, its earlier versions, the outline and the approval.

Approval is tied to the exact bytes of script.json. Editing the script by hand, revising it
or regenerating it after approval puts it back in draft.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .errors import PipelineError
from .series import Series, episode_dir_name

VERSION_RE = re.compile(r"^script\.v(\d+)\.json$")


@dataclass
class Episode:
    number: int
    path: Path

    @classmethod
    def of(cls, series: Series, number: int) -> "Episode":
        return cls(number=number, path=series.episodes_dir / episode_dir_name(number))

    @property
    def script_path(self) -> Path:
        return self.path / "script.json"

    @property
    def outline_path(self) -> Path:
        return self.path / "outline.txt"

    @property
    def approval_path(self) -> Path:
        return self.path / "approved.json"

    def has_script(self) -> bool:
        return self.script_path.is_file()

    # --- outline -------------------------------------------------------------

    def save_outline(self, outline: str) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self.outline_path.write_text(outline.strip() + "\n", encoding="utf-8")

    def load_outline(self) -> str | None:
        if self.outline_path.is_file():
            return self.outline_path.read_text(encoding="utf-8").strip() or None
        return None

    # --- script --------------------------------------------------------------

    def load_script_data(self) -> object:
        try:
            return json.loads(self.script_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise PipelineError(
                f"{self.script_path} is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}"
            ) from e

    def save_script(self, data: dict) -> Path | None:
        """Write script.json, first keeping any existing one as script.vN.json. Returns the backup path."""
        self.path.mkdir(parents=True, exist_ok=True)
        backup = None
        if self.script_path.exists():
            backup = self.path / f"script.v{self._next_version()}.json"
            shutil.copy2(self.script_path, backup)
        self.script_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return backup

    def versions(self) -> list[Path]:
        found = [(int(m.group(1)), p) for p in self.path.glob("script.v*.json") if (m := VERSION_RE.match(p.name))]
        return [p for _, p in sorted(found)]

    def _next_version(self) -> int:
        nums = [int(VERSION_RE.match(p.name).group(1)) for p in self.versions()]
        return max(nums, default=0) + 1

    # --- approval ------------------------------------------------------------

    def _script_hash(self) -> str:
        return hashlib.sha256(self.script_path.read_bytes()).hexdigest()

    def approve(self) -> None:
        record = {"script_sha256": self._script_hash(), "approved_at": datetime.now(timezone.utc).isoformat()}
        self.approval_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    def approval_status(self) -> str:
        """'approved', 'draft', or 'changed' (approved once, edited since)."""
        if not self.approval_path.is_file() or not self.has_script():
            return "draft"
        try:
            record = json.loads(self.approval_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return "draft"
        return "approved" if record.get("script_sha256") == self._script_hash() else "changed"

    def is_approved(self) -> bool:
        return self.approval_status() == "approved"

    def require_approved(self) -> None:
        """Later (paid) steps call this before spending anything."""
        if not self.is_approved():
            raise PipelineError(
                f"The script for {self.path.name} is not approved. Review {self.script_path}, "
                "then run again with --approve."
            )
