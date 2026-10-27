"""Make labeled cover images (series name + part number) for finished episodes. Free: no API calls.

    python tools/make_covers.py                          # every episode with a final.mp4, all series
    python tools/make_covers.py --series nuke-new-york   # one series
    python tools/make_covers.py --series nuke-new-york --episode 2

Each cover is the episode's hook image (images/01.png) with a colored series label and "PART N" in the
middle third, so the label survives Instagram's square grid crop. Saved as episodes/epNN/cover.png
(1080x1920), plus a copy in covers/ at the project root named <series>-epNN.png for easy AirDrop/upload.

Per-series label and color come from series/<name>/cover.yaml (optional):

    label: "NUKE NYC"
    color: "#FFD400"

Without it, the label is the series title in capitals and the color is picked from a fixed palette.
"""

import argparse
import hashlib
import sys
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
W, H = 1080, 1920
PALETTE = ["#FFD400", "#FF6A00", "#00C2B8", "#2F8CFF", "#E040FB", "#7CFC00"]


def font(size: int) -> ImageFont.ImageFont:
    fonts = sorted((ROOT / "assets" / "fonts").glob("*.[ot]tf"))
    return ImageFont.truetype(str(fonts[0]), size) if fonts else ImageFont.load_default(size)


def series_style(series_dir: Path) -> tuple[str, str]:
    meta = yaml.safe_load((series_dir / "series.yaml").read_text(encoding="utf-8")) or {}
    title = str(meta.get("title") or series_dir.name.replace("-", " "))
    cfg_path = series_dir / "cover.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
    cfg = cfg or {}
    default_color = PALETTE[int(hashlib.md5(series_dir.name.encode()).hexdigest(), 16) % len(PALETTE)]
    return str(cfg.get("label") or title.upper()), str(cfg.get("color") or default_color)


def text_color_for(hex_color: str) -> str:
    r, g, b = (int(hex_color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return "#111111" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#FFFFFF"


def fit_font(draw: ImageDraw.ImageDraw, text: str, max_w: int, start: int) -> ImageFont.ImageFont:
    size = start
    while size > 40:
        f = font(size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 6
    return font(size)


def make_cover(image_path: Path, label: str, color: str, part: int) -> Image.Image:
    img = ImageOps.fit(Image.open(image_path).convert("RGB"), (W, H))
    # Darken the middle band slightly so the label pops on any background.
    shade = Image.new("L", (1, H), 0)
    for y in range(H):
        d = abs(y - 960) / 520  # strongest at the label, fading out smoothly above and below
        shade.putpixel((0, y), int(110 * max(0.0, 1 - d * d)))
    img = Image.composite(Image.new("RGB", (W, H), "black"), img, shade.resize((W, H)))

    draw = ImageDraw.Draw(img)
    # Series label on a solid colored box (centered horizontally, upper middle of the square crop).
    f_label = fit_font(draw, label, W - 220, 150)
    lw = draw.textlength(label, font=f_label)
    l_top, l_bottom = draw.textbbox((0, 0), label, font=f_label, anchor="ls")[1::2]
    pad_x, pad_y = 50, 30
    box_h = (l_bottom - l_top) + 2 * pad_y
    box_w = lw + 2 * pad_x
    box_y = 700
    box_x = (W - box_w) / 2
    draw.rounded_rectangle([box_x, box_y, box_x + box_w, box_y + box_h], radius=28, fill=color)
    draw.text((W / 2, box_y + pad_y - l_top), label, font=f_label, fill=text_color_for(color), anchor="ms")

    # Part number below in white with a dark outline, plus a thin underline in the series color.
    part_text = f"PART {part}"
    f_part = font(210)
    part_y = box_y + box_h + 50
    draw.text((W / 2, part_y), part_text, font=f_part, fill="white", anchor="mt",
              stroke_width=10, stroke_fill="black")
    pb = draw.textbbox((W / 2, part_y), part_text, font=f_part, anchor="mt")
    draw.rectangle([pb[0], pb[3] + 22, pb[2], pb[3] + 36], fill=color)
    return img


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--series", help="series folder name (default: all)")
    p.add_argument("--episode", type=int, help="episode number (default: all finished episodes)")
    p.add_argument("--all", action="store_true", help="include episodes without a final.mp4")
    args = p.parse_args()

    series_root = ROOT / "series"
    dirs = [series_root / args.series] if args.series else sorted(d for d in series_root.iterdir() if d.is_dir())
    out_dir = ROOT / "covers"
    out_dir.mkdir(exist_ok=True)
    made = 0
    for sdir in dirs:
        if not (sdir / "series.yaml").is_file():
            continue
        label, color = series_style(sdir)
        for ep in sorted((sdir / "episodes").glob("ep[0-9]*")):
            n = int(ep.name[2:])
            if args.episode and n != args.episode:
                continue
            hook = ep / "images" / "01.png"
            if not hook.is_file() or (not args.all and not (ep / "final.mp4").is_file()):
                continue
            cover = make_cover(hook, label, color, n)
            cover.save(ep / "cover.png")
            cover.save(out_dir / f"{sdir.name}-{ep.name}.png")
            print(f"{sdir.name}/{ep.name}: {label} · PART {n}")
            made += 1
    if not made:
        sys.exit("No finished episodes found.")
    print(f"{made} covers saved to covers/ and each episode folder.")


if __name__ == "__main__":
    main()
