"""Draw profile-picture candidates in a series' art style (one-off branding helper, not part of the pipeline).

    python tools/make_profile_pics.py --series stranded-at-sea           # 4 candidates, about $0.30 total
    python tools/make_profile_pics.py --series stranded-at-sea --quality high

Saves square PNGs to assets/branding/. Uses OPENAI_API_KEY and IMAGE_MODEL from .env.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.imagegen import make_image_client  # noqa: E402

COMMON = (" This is a social media profile picture shown as a small circle: one bold, simple subject, centered and"
          " filling the middle of the frame, nothing important near the corners, strong contrast and a clean"
          " silhouette that still reads at 100 pixels wide. No text, no logos, no border.")

VARIANTS = {
    "survivor-portrait": ("Head-and-shoulders portrait of the survivor from the reference image, wearing the wide-brim "
        "hat, facing three-quarters toward the viewer with a determined, weary gaze. Behind: a deep blue dusk sky "
        "and a low warm sun glow, softly out of focus; no ocean or boat.", True),
    "survivor-hat-closeup": ("Tight close-up of the survivor from the reference image: the wide-brim hat brim and "
        "determined eyes fill the frame, warm rim light from a low sun on one side, cool deep-blue shadow on the "
        "other, plain dark blue background.", True),
    "ridge-sunrise": ("A lone survivor in a wide-brim hat with a small backpack and a walking stick, standing on a bare "
        "ridge as a dark silhouette against a huge rising sun, rolling hills fading into the distance, warm orange sky "
        "fading to deep blue. No ocean, no boat: the setting should feel like any wilderness.", False),
    "survivor-sunrise-portrait": ("Head-and-shoulders portrait of the survivor from the reference image, wearing the "
        "wide-brim hat, half lit by a low rising sun, looking toward the horizon, determined. Plain dark blue "
        "background with a warm glow; no ocean, no boat, no specific landscape.", True),
}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--series", required=True)
    p.add_argument("--ref", default="survivor", help="ref image used for the character (default: survivor)")
    p.add_argument("--quality", default="medium", choices=["low", "medium", "high"])
    args = p.parse_args()

    series = ROOT / "series" / args.series
    style = (series / "style_line.txt").read_text(encoding="utf-8").strip()
    style = style.replace("Vertical 9:16 frame.", "Square 1:1 frame.")
    ref = next((series / "refs" / f"{args.ref}{s}" for s in (".png", ".jpg", ".jpeg", ".webp")
                if (series / "refs" / f"{args.ref}{s}").is_file()), None)
    out = ROOT / "assets" / "branding"
    out.mkdir(parents=True, exist_ok=True)

    client = make_image_client(False, ROOT, args.quality)
    total = 0.0
    for name, (prompt, use_ref) in VARIANTS.items():
        image = client.generate(f"{style}\n\n{prompt}{COMMON}", "1024x1024", [ref] if use_ref and ref else [])
        path = out / f"{args.series}-{name}.png"
        path.write_bytes(image.png)
        cost = image.cost(client.model) or 0.0
        total += cost
        print(f"{path.relative_to(ROOT)}  ${cost:.3f}")
    print(f"Total: ${total:.2f}")


if __name__ == "__main__":
    main()
