#!/usr/bin/env python3
"""Deterministic Pillow renderer for exact text and product compositing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FONT = SKILL_ROOT / "assets" / "fonts" / "inter" / "Inter-Variable.ttf"


def color(value: str, opacity: int | None = None) -> tuple[int, int, int, int]:
    rgb = ImageColor.getrgb(value)
    if len(rgb) == 4:
        return rgb
    return (*rgb, 255 if opacity is None else opacity)


def resolve_path(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def fit_image(source: Image.Image, size: tuple[int, int], mode: str) -> Image.Image:
    sw, sh = source.size
    tw, th = size
    scale = max(tw / sw, th / sh) if mode == "cover" else min(tw / sw, th / sh)
    resized = source.resize((max(1, round(sw * scale)), max(1, round(sh * scale))), Image.Resampling.LANCZOS)
    if mode == "cover":
        left = max(0, (resized.width - tw) // 2)
        top = max(0, (resized.height - th) // 2)
        return resized.crop((left, top, left + tw, top + th))
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(resized, ((tw - resized.width) // 2, (th - resized.height) // 2))
    return canvas


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return "\n".join(lines)


def add_shadow(canvas: Image.Image, image: Image.Image, position: tuple[int, int], spec: dict) -> None:
    shadow_spec = spec.get("shadow")
    if not shadow_spec:
        return
    alpha = image.getchannel("A")
    shadow = Image.new("RGBA", image.size, color(shadow_spec.get("color", "#000000"), shadow_spec.get("opacity", 80)))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(float(shadow_spec.get("blur", 18)))))
    offset = shadow_spec.get("offset", [0, 12])
    canvas.alpha_composite(shadow, (position[0] + int(offset[0]), position[1] + int(offset[1])))


def render(spec_path: Path, output_override: Path | None = None) -> Path:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    base = spec_path.parent
    canvas_spec = spec["canvas"]
    size = (int(canvas_spec["width"]), int(canvas_spec["height"]))
    canvas = Image.new("RGBA", size, color(canvas_spec.get("background", "#FFFFFF")))

    for layer in spec.get("layers", []):
        kind = layer["type"]
        if kind == "rectangle":
            overlay = Image.new("RGBA", size, (0, 0, 0, 0))
            d = ImageDraw.Draw(overlay)
            box = tuple(map(int, layer["box"]))
            d.rounded_rectangle(box, radius=int(layer.get("radius", 0)), fill=color(layer["fill"], layer.get("opacity")))
            canvas.alpha_composite(overlay)
        elif kind == "line":
            d = ImageDraw.Draw(canvas)
            d.line(tuple(map(int, layer["points"])), fill=color(layer["fill"]), width=int(layer.get("width", 2)))
        elif kind == "image":
            source = Image.open(resolve_path(base, layer["path"])).convert("RGBA")
            box = list(map(int, layer["box"]))
            fitted = fit_image(source, (box[2], box[3]), layer.get("fit", "contain"))
            if "opacity" in layer:
                fitted.putalpha(fitted.getchannel("A").point(lambda x: round(x * float(layer["opacity"]))))
            add_shadow(canvas, fitted, (box[0], box[1]), layer)
            canvas.alpha_composite(fitted, (box[0], box[1]))
        elif kind == "text":
            font_path = resolve_path(base, layer["font"]) if layer.get("font") else DEFAULT_FONT
            font = ImageFont.truetype(str(font_path), int(layer["size"]))
            if hasattr(font, "set_variation_by_axes") and layer.get("weight"):
                try:
                    font.set_variation_by_axes([14, int(layer["weight"])])
                except (OSError, ValueError):
                    pass
            d = ImageDraw.Draw(canvas)
            x, y = map(int, layer["position"])
            max_width = int(layer.get("max_width", size[0] - x))
            text = str(layer["text"])
            if layer.get("uppercase"):
                text = text.upper()
            wrapped = wrap_text(d, text, font, max_width)
            d.multiline_text(
                (x, y), wrapped, font=font, fill=color(layer.get("fill", "#111111")),
                spacing=int(layer.get("spacing", round(int(layer["size"]) * 0.18))),
                align=layer.get("align", "left"),
                stroke_width=int(layer.get("stroke_width", 0)),
                stroke_fill=color(layer.get("stroke_fill", "#000000")),
            )
        else:
            raise ValueError(f"unsupported layer type: {kind}")

    output = output_override or resolve_path(base, spec["output"])
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() in {".jpg", ".jpeg"}:
        canvas.convert("RGB").save(output, quality=int(spec.get("jpeg_quality", 94)), optimize=True)
    else:
        canvas.save(output, optimize=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = render(args.spec.resolve(), args.output.resolve() if args.output else None)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
