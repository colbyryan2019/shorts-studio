"""A throwaway project with one small series, so tests never touch the real series/ folder."""

import copy
from pathlib import Path

import pytest

from pipeline.llm import FAKE_SCRIPT

SERIES_YAML = """\
title: Test Series
voice_id: ""
words: [90, 110]
shots: [8, 12]
max_genvideo_shots: 2
end_card_format: "DAY {day} · WATER: {water}"
default_motion: rock
caption_style: bold_white_outline
music_volume_db: -20
sources_required: true
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "house_rules.md").write_text("# House Rules\n- Second person only.\n")
    s = tmp_path / "series" / "test-series"
    (s / "sources").mkdir(parents=True)
    (s / "refs").mkdir()
    (s / "series.yaml").write_text(SERIES_YAML)
    (s / "bible.md").write_text("# Bible\nYou are lost at sea.\n")
    (s / "style_line.txt").write_text("Stick figures.\n")
    (s / "sources" / "survival-manual.txt").write_text("Never drink seawater.\n")
    (s / "refs" / "dinghy.png").write_bytes(b"png")
    return tmp_path


@pytest.fixture
def series_dir(project: Path) -> Path:
    return project / "series" / "test-series"


@pytest.fixture
def good_script() -> dict:
    return copy.deepcopy(FAKE_SCRIPT)
