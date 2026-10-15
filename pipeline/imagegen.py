"""Image generation behind one small interface, plus fitting images to the 1080x1920 frame."""

from __future__ import annotations

import base64
import io
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import openai
from PIL import Image, ImageDraw, ImageOps

from .config import load_settings
from .errors import PipelineError

FRAME = (1080, 1920)
FAKE_MODEL = "fake"

# Rough cost per image by quality, for --dry-run estimates only (portrait, reference images included).
# Real costs are computed from the token usage each call returns.
ROUGH_COST_PER_IMAGE = {"low": 0.01, "medium": 0.07, "high": 0.25}

# USD per 1M tokens: (image input, text input, image output). Unknown models report tokens only.
TOKEN_RATES = {
    "gpt-image-2.5-sunburst": (8.00, 5.00, 30.00),
    "gpt-image-2.5-flare": (8.00, 5.00, 30.00),
    "gpt-image-2": (8.00, 5.00, 30.00),
}


@dataclass
class GeneratedImage:
    png: bytes
    input_image_tokens: int = 0
    input_text_tokens: int = 0
    output_tokens: int = 0

    def cost(self, model: str) -> float | None:
        if model == FAKE_MODEL:
            return 0.0
        rates = TOKEN_RATES.get(model)
        if rates is None:
            return None
        image_in, text_in, out = rates
        return (self.input_image_tokens * image_in + self.input_text_tokens * text_in + self.output_tokens * out) / 1e6


@dataclass
class Budget:
    """Stops a run before the next image would push spending past the limit.

    The next image is assumed to cost as much as the most expensive one so far this run.
    """

    model: str
    limit: float
    spent: float = 0.0
    largest: float = 0.0

    def __post_init__(self):
        if self.model != FAKE_MODEL and self.model not in TOKEN_RATES:
            raise PipelineError(
                f"No prices known for image model '{self.model}', so the IMAGE_COST_LIMIT can't be enforced. "
                "Add its token rates to TOKEN_RATES in pipeline/imagegen.py."
            )

    def check_next(self) -> None:
        if self.spent + self.largest > self.limit:
            raise PipelineError(
                f"Stopped: ${self.spent:.2f} spent this run, and the next image (~${self.largest:.2f}) would pass "
                f"the ${self.limit:.2f} limit (IMAGE_COST_LIMIT in .env). Everything drawn so far is saved; "
                "run the same command again to continue, or raise the limit."
            )

    def add(self, cost: float | None) -> None:
        cost = cost or 0.0
        self.spent += cost
        self.largest = max(self.largest, cost)


def make_budget(model: str, root: Path) -> Budget:
    limit = load_settings(root).image_cost_limit if model != FAKE_MODEL else float("inf")
    return Budget(model=model, limit=limit)


class ImageClient(Protocol):
    model: str

    def generate(self, prompt: str, size: str, refs: list[Path]) -> GeneratedImage:
        """One image from a prompt, guided by the reference images (if any)."""
        ...


class OpenAIImageClient:
    def __init__(self, api_key: str, model: str, quality: str):
        # Key passed explicitly; max_retries gives backoff on rate limits and server errors.
        self._client = openai.OpenAI(api_key=api_key, max_retries=4, timeout=300)
        self.model = model
        self.quality = quality

    def generate(self, prompt: str, size: str, refs: list[Path]) -> GeneratedImage:
        try:
            if refs:
                files = [open(p, "rb") for p in refs]
                try:
                    response = self._client.images.edit(
                        model=self.model, image=files, prompt=prompt, size=size,
                        quality=self.quality, output_format="png", n=1,
                    )
                finally:
                    for f in files:
                        f.close()
            else:
                response = self._client.images.generate(
                    model=self.model, prompt=prompt, size=size, quality=self.quality, output_format="png", n=1,
                )
        except openai.AuthenticationError as e:
            raise PipelineError("OpenAI rejected OPENAI_API_KEY. Check the key in .env.") from e
        except openai.NotFoundError as e:
            raise PipelineError(f"Image model '{self.model}' not found. Check IMAGE_MODEL in .env.") from e
        except openai.BadRequestError as e:
            raise PipelineError(f"OpenAI rejected the image request: {e.message}") from e
        except openai.APIStatusError as e:
            raise PipelineError(f"OpenAI API error {e.status_code} (after retries): {e.message}") from e
        except openai.APIConnectionError as e:
            raise PipelineError("Could not reach the OpenAI API. Check your internet connection.") from e

        if not response.data or not response.data[0].b64_json:
            raise PipelineError("OpenAI returned no image data.")
        image = GeneratedImage(png=base64.b64decode(response.data[0].b64_json))
        if response.usage:
            details = response.usage.input_tokens_details
            image.input_image_tokens = getattr(details, "image_tokens", 0) or 0
            image.input_text_tokens = getattr(details, "text_tokens", 0) or 0
            image.output_tokens = response.usage.output_tokens or 0
        return image


@dataclass
class FakeImageClient:
    """Draws a labeled placeholder instead of calling an API. Records each call for tests."""

    model: str = FAKE_MODEL
    calls: list[tuple[str, str, list[Path]]] = field(default_factory=list)

    def generate(self, prompt: str, size: str, refs: list[Path]) -> GeneratedImage:
        self.calls.append((prompt, size, list(refs)))
        width, height = (int(v) for v in size.split("x"))
        img = Image.new("RGB", (width, height), (240, 236, 226))
        draw = ImageDraw.Draw(img)
        refs_line = "refs: " + (", ".join(p.stem for p in refs) or "none")
        text = "FAKE IMAGE\n\n" + "\n".join(textwrap.wrap(prompt[-400:], 40)) + "\n\n" + refs_line
        draw.multiline_text((40, 40), text, fill=(20, 20, 20), spacing=8)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return GeneratedImage(png=buf.getvalue())


def make_image_client(fake: bool, root: Path, quality: str) -> ImageClient:
    if fake:
        return FakeImageClient()
    settings = load_settings(root)
    if settings.image_provider != "openai":
        raise PipelineError(
            f"IMAGE_PROVIDER={settings.image_provider} isn't supported yet; set IMAGE_PROVIDER=openai in .env."
        )
    if not settings.openai_api_key:
        raise PipelineError(
            f"OPENAI_API_KEY is not set. Add it to {root / '.env'} (or run with --fake-images to test without one)."
        )
    if not settings.image_model:
        raise PipelineError(
            f"IMAGE_MODEL is not set. Add an image model id to {root / '.env'}, e.g. IMAGE_MODEL=gpt-image-2.5-sunburst"
        )
    return OpenAIImageClient(api_key=settings.openai_api_key, model=settings.image_model, quality=quality)


def fit_to_frame(png: bytes, frame: tuple[int, int] = FRAME) -> Image.Image:
    """Scale to fit inside the frame and pad the rest with the image's own edge color."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    if img.size == frame:
        return img
    fitted = ImageOps.contain(img, frame, method=Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", frame, _edge_color(img))
    canvas.paste(fitted, ((frame[0] - fitted.width) // 2, (frame[1] - fitted.height) // 2))
    return canvas


def _edge_color(img: Image.Image) -> tuple[int, int, int]:
    w, h = img.size
    pixels = [img.getpixel((x, y)) for x in range(0, w, max(1, w // 20)) for y in (0, h - 1)]
    pixels += [img.getpixel((x, y)) for y in range(0, h, max(1, h // 20)) for x in (0, w - 1)]
    return tuple(sum(p[i] for p in pixels) // len(pixels) for i in range(3))
