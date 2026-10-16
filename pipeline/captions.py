"""Captions and the end card, drawn as transparent 1080x1920 PNGs that assembly lays over the video.

Drawn with Pillow rather than ffmpeg's subtitle filters, so any ffmpeg build works. Timings come
from the narration (no transcription): each line is split into short phrases, shown one at a time,
each for a share of the line's spoken length in proportion to its characters.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .errors import PipelineError
from .ffmpeg import HEIGHT, WIDTH

FONT_SUFFIXES = {".ttf", ".otf"}
END_CARD_SECONDS = 2.0
MAX_PHRASE_WORDS = 4
MAX_PHRASE_CHARS = 22
TEXT_WIDTH = WIDTH - 160  # keep text clear of the frame edges


@dataclass(frozen=True)
class TextStyle:
    size: int
    fill: tuple[int, int, int]
    stroke: int  # outline width in px
    stroke_fill: tuple[int, int, int]
    center_y: float  # vertical centre of the text, as a fraction of the frame height
    upper: bool


# Named styles for series.yaml's caption_style. Captions sit in the lower middle, clear of the
# platform buttons and description at the bottom of the screen.
CAPTION_STYLES = {
    "bold_white_outline": TextStyle(size=92, fill=(255, 255, 255), stroke=9, stroke_fill=(0, 0, 0),
                                    center_y=0.66, upper=True),
    "plain_white": TextStyle(size=80, fill=(255, 255, 255), stroke=3, stroke_fill=(0, 0, 0),
                             center_y=0.66, upper=False),
}
DEFAULT_CAPTION_STYLE = "bold_white_outline"
END_CARD_STYLE = TextStyle(size=104, fill=(255, 255, 255), stroke=4, stroke_fill=(0, 0, 0),
                           center_y=0.42, upper=True)


@dataclass
class Overlay:
    png: Path
    start: float
    end: float


def caption_style(name: str) -> TextStyle:
    name = name or DEFAULT_CAPTION_STYLE
    if name not in CAPTION_STYLES:
        raise PipelineError(f"caption_style '{name}' is not known. Use one of: {', '.join(CAPTION_STYLES)}")
    return CAPTION_STYLES[name]


def find_font(fonts_dir: Path) -> Path | None:
    """The first .ttf/.otf in assets/fonts, or None (then Pillow's built-in font is used)."""
    if not fonts_dir.is_dir():
        return None
    found = sorted(f for f in fonts_dir.iterdir() if f.suffix.lower() in FONT_SUFFIXES and not f.name.startswith("."))
    return found[0] if found else None


def split_phrases(text: str) -> list[str]:
    """Short caption phrases: split at punctuation, then each clause into evenly sized groups of words."""
    phrases = []
    for clause in re.findall(r"\S.*?(?:[.!?;:,—](?=\s|$)|$)", text.strip()):
        words = clause.split()
        if not words:
            continue
        groups = max(math.ceil(len(words) / MAX_PHRASE_WORDS), math.ceil(len(clause) / MAX_PHRASE_CHARS))
        groups = min(groups, len(words))
        for i in range(groups):  # spread the words evenly, so no one-word stragglers
            phrases.append(" ".join(words[i * len(words) // groups:(i + 1) * len(words) // groups]))
    return phrases


def phrase_times(phrases: list[str], start: float, line_seconds: float, shot_end: float) -> list[tuple[float, float]]:
    """(start, end) per phrase, splitting the spoken time by character count; the last holds to the shot's end."""
    weights = [len(p) + 2 for p in phrases]
    total = sum(weights)
    times, t = [], start
    for i, w in enumerate(weights):
        end = shot_end if i == len(weights) - 1 else t + line_seconds * w / total
        times.append((round(t, 3), round(end, 3)))
        t = end
    return times


def _font(font_path: Path | None, size: int) -> ImageFont.FreeTypeFont:
    if font_path is None:
        return ImageFont.load_default(size=size)
    try:
        return ImageFont.truetype(str(font_path), size)
    except OSError as e:
        raise PipelineError(f"Could not load the caption font {font_path}: {e}") from e


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, stroke: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(candidate, font=font) + 2 * stroke > TEXT_WIDTH:
            lines.append(current)
            current = word
        else:
            current = candidate
    return lines + [current] if current else lines


def render_text(text: str, style: TextStyle, font_path: Path | None, out: Path) -> None:
    text = text.upper() if style.upper else text
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    size = style.size
    font = _font(font_path, size)
    lines = _wrap(draw, text, font, style.stroke)
    while len(lines) > 1 and size > style.size * 0.75:  # one line reads faster: shrink a little to fit
        size -= 4
        font = _font(font_path, size)
        lines = _wrap(draw, text, font, style.stroke)
    if len(lines) > 1:  # still too long: wrap at full size rather than tiny text
        size = style.size
        font = _font(font_path, size)
        lines = _wrap(draw, text, font, style.stroke)
        if " · " in text:  # e.g. an end card "DAY 1 · WATER: NONE": break at the separators
            lines = [line for part in text.split(" · ") for line in _wrap(draw, part, font, style.stroke)]
    while len(lines) > 2 and size > 40:  # shrink rather than stack many lines
        size -= 6
        font = _font(font_path, size)
        lines = _wrap(draw, text, font, style.stroke)
    draw.multiline_text(
        (WIDTH / 2, HEIGHT * style.center_y), "\n".join(lines), font=font, anchor="mm", align="center",
        fill=style.fill, stroke_width=style.stroke, stroke_fill=style.stroke_fill, spacing=size // 5,
    )
    img.save(out, format="PNG")


def build_overlays(timings: list[tuple[str, float, float]], end_card: str, style: TextStyle,
                   font_path: Path | None, out_dir: Path) -> list[Overlay]:
    """Caption PNGs for every phrase plus the end card.

    `timings` is (narration, line_seconds, shot_seconds) per shot, in order.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    overlays, t, k = [], 0.0, 0
    for text, line_seconds, shot_seconds in timings:
        phrases = split_phrases(text)
        for phrase, (start, end) in zip(phrases, phrase_times(phrases, t, line_seconds, t + shot_seconds)):
            k += 1
            png = out_dir / f"cap{k:03d}.png"
            render_text(phrase, style, font_path, png)
            overlays.append(Overlay(png, start, end))
        t += shot_seconds
    card = out_dir / "end_card.png"
    render_text(end_card, END_CARD_STYLE, font_path, card)
    overlays.append(Overlay(card, round(max(t - END_CARD_SECONDS, 0.0), 3), round(t, 3)))
    return overlays
