"""Captions, stamps and the end card, drawn as transparent 1080x1920 PNGs that assembly lays over the video.

Drawn with Pillow rather than ffmpeg's subtitle filters, so any ffmpeg build works. Timings come
from the narration (no transcription): each line is split into short phrases, shown one at a time.
`highlight_word` shows each phrase with the word being spoken in a highlight colour, timed from the
voice's word timings; the other styles give each phrase a share of the line's spoken length in
proportion to its characters (as does `highlight_word` for a line without word timings).
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
    highlight: tuple[int, int, int] | None = None  # colour of the word being spoken (word-timed styles)


# Named styles for series.yaml's caption_style. Captions sit in the lower middle, clear of the
# platform buttons and description at the bottom of the screen.
CAPTION_STYLES = {
    "bold_white_outline": TextStyle(size=92, fill=(255, 255, 255), stroke=9, stroke_fill=(0, 0, 0),
                                    center_y=0.66, upper=True),
    "plain_white": TextStyle(size=80, fill=(255, 255, 255), stroke=3, stroke_fill=(0, 0, 0),
                             center_y=0.66, upper=False),
    "highlight_word": TextStyle(size=96, fill=(255, 255, 255), stroke=8, stroke_fill=(0, 0, 0),
                                center_y=0.62, upper=True, highlight=(255, 210, 40)),
}
DEFAULT_CAPTION_STYLE = "bold_white_outline"
END_CARD_STYLE = TextStyle(size=104, fill=(255, 255, 255), stroke=4, stroke_fill=(0, 0, 0),
                           center_y=0.42, upper=True)
# The follow line (series.yaml end_card_cta), between the end card and the captions.
CTA_STYLE = TextStyle(size=64, fill=(255, 210, 40), stroke=4, stroke_fill=(0, 0, 0), center_y=0.53, upper=True)
# A shot's stamp (e.g. a countdown), near the top but below the platform's header.
STAMP_STYLE = TextStyle(size=84, fill=(255, 64, 48), stroke=7, stroke_fill=(0, 0, 0), center_y=0.13, upper=True)


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


def find_font(fonts_dir: Path, name: str = "") -> Path | None:
    """`name` from assets/fonts if given, else the first .ttf/.otf there, or None (Pillow's built-in font)."""
    if name:
        path = fonts_dir / name
        if not path.is_file():
            raise PipelineError(f"caption_font '{name}' is not in {fonts_dir}")
        return path
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


def _fit(draw: ImageDraw.ImageDraw, text: str, style: TextStyle, font_path: Path | None):
    """(font, size, lines) for `text`: one line if a slightly smaller size fits, else at most two."""
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
    return font, size, lines


def render_text(text: str, style: TextStyle, font_path: Path | None, out: Path) -> None:
    text = text.upper() if style.upper else text
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font, size, lines = _fit(draw, text, style, font_path)
    draw.multiline_text(
        (WIDTH / 2, HEIGHT * style.center_y), "\n".join(lines), font=font, anchor="mm", align="center",
        fill=style.fill, stroke_width=style.stroke, stroke_fill=style.stroke_fill, spacing=size // 5,
    )
    img.save(out, format="PNG")


def render_words(words: list[str], highlight: int, style: TextStyle, font_path: Path | None, out: Path) -> None:
    """A phrase drawn word by word, so the word at index `highlight` can take the highlight colour."""
    words = [w.upper() if style.upper else w for w in words]
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font, size, lines = _fit(draw, " ".join(words), style, font_path)
    space = draw.textlength(" ", font=font)
    line_height = size + size // 5
    y = HEIGHT * style.center_y - line_height * (len(lines) - 1) / 2
    i = 0
    for line in lines:
        line_words = line.split()
        x = WIDTH / 2 - draw.textlength(line, font=font) / 2
        for word in line_words:
            colour = style.highlight if i == highlight and style.highlight else style.fill
            draw.text((x, y), word, font=font, anchor="lm", fill=colour,
                      stroke_width=style.stroke, stroke_fill=style.stroke_fill)
            x += draw.textlength(word, font=font) + space
            i += 1
        y += line_height
    img.save(out, format="PNG")


def word_segments(text: str, words: list[tuple[str, float, float]] | None, start: float,
                  shot_end: float) -> list[tuple[list[str], int, float, float]] | None:
    """(phrase words, index of the spoken word, start, end) per spoken word, or None if the timings
    don't line up with the text. Each word stays highlighted until the next one starts; the last
    holds to the end of the shot."""
    phrases = [p.split() for p in split_phrases(text)]
    if not words or sum(len(p) for p in phrases) != len(words):
        return None
    segments, k = [], 0
    for phrase in phrases:
        for i in range(len(phrase)):
            seg_start = start if k == 0 else start + words[k][1]
            seg_end = shot_end if k == len(words) - 1 else start + words[k + 1][1]
            segments.append((phrase, i, round(seg_start, 3), round(seg_end, 3)))
            k += 1
    return segments


def build_overlays(timings: list[tuple[str, float, float, list | None]], end_card: str, style: TextStyle,
                   font_path: Path | None, out_dir: Path, cta: str = "") -> tuple[list[Overlay], Overlay]:
    """Caption PNGs (in order, never overlapping) and the end card.

    `timings` is (narration, line_seconds, shot_seconds, word timings or None) per shot, in order.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    captions, t, k = [], 0.0, 0
    for text, line_seconds, shot_seconds, words in timings:
        segments = word_segments(text, words, t, t + shot_seconds) if style.highlight else None
        if segments:
            for phrase, i, start, end in segments:
                k += 1
                png = out_dir / f"cap{k:03d}.png"
                render_words(phrase, i, style, font_path, png)
                captions.append(Overlay(png, start, end))
        else:
            phrases = split_phrases(text)
            for phrase, (start, end) in zip(phrases, phrase_times(phrases, t, line_seconds, t + shot_seconds)):
                k += 1
                png = out_dir / f"cap{k:03d}.png"
                render_text(phrase, style, font_path, png)
                captions.append(Overlay(png, start, end))
        t += shot_seconds
    card = out_dir / "end_card.png"
    render_text(end_card, END_CARD_STYLE, font_path, card)
    if cta:
        line = out_dir / "end_card_cta.png"
        render_text(cta, CTA_STYLE, font_path, line)
        with Image.open(card) as base, Image.open(line) as top:
            Image.alpha_composite(base, top).save(card, format="PNG")
    return captions, Overlay(card, round(max(t - END_CARD_SECONDS, 0.0), 3), round(t, 3))


def build_stamps(stamps: list[str | None], shot_seconds: list[float], font_path: Path | None, out_dir: Path,
                 until: float) -> list[Overlay]:
    """Stamp PNGs, in order. A shot's stamp holds until a later shot sets another ("" hides it), and
    none shows past `until` (the end card). Call after build_overlays, which clears out_dir."""
    spans, t = [], 0.0
    for text, secs in zip(stamps, shot_seconds):
        if text is not None and (not spans or spans[-1][0] != text):
            spans.append((text, t))
        t += secs
    overlays = []
    for i, (text, start) in enumerate(spans):
        end = min(spans[i + 1][1] if i + 1 < len(spans) else until, until)
        if not text or start >= end:
            continue
        png = out_dir / f"stamp{i + 1:02d}.png"
        render_text(text, STAMP_STYLE, font_path, png)
        overlays.append(Overlay(png, round(start, 3), round(end, 3)))
    return overlays
