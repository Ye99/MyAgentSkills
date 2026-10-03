#!/usr/bin/env python3
"""Deskew a scanned document from its printed text lines, crop it, keep print size.

  info    size and DPI of the scan (from the JPEG, else a sibling PDF)
  grid    preview of the (optionally rotated) scan or a region of it, with
          gridlines labelled in full-resolution pixels - for picking regions
  angle   fit the baseline of every text line inside a region; prints the
          rotation that levels them (PIL convention, + = counterclockwise)
  edges   snap a rough crop box (read off a grid preview) to the real edges
  apply   rotate, crop and save one JPEG at the source DPI and quality
          (never larger than the source), plus previews
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, JpegImagePlugin

Image.MAX_IMAGE_PIXELS = None


def parse_box(text: str) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = (int(float(v)) for v in text.split(","))
    return x0, y0, x1, y1


def source_dpi(path: Path) -> float | None:
    dpi = Image.open(path).info.get("dpi")
    if dpi and dpi[0] > 1:
        return float(dpi[0])
    pdf = path.with_suffix(".pdf")
    if pdf.exists():  # scanners often save a sibling PDF that records the DPI
        out = subprocess.run(["pdfimages", "-list", str(pdf)], capture_output=True, text=True).stdout
        for line in out.splitlines()[2:]:
            cols = line.split()
            if len(cols) > 12 and cols[2] == "image" and cols[12].isdigit():
                return float(cols[12])
    return None


def rotate(img: Image.Image, angle: float) -> Image.Image:
    if not angle:
        return img
    return img.rotate(angle, resample=Image.BICUBIC, fillcolor=(255, 255, 255))


def grid_preview(img: Image.Image, out: Path, region=None, max_px: int = 1000) -> None:
    x0, y0, x1, y1 = region or (0, 0, img.width, img.height)
    view = img.crop((x0, y0, x1, y1))
    scale = max_px / max(view.size)
    view = view.resize((max(1, round(view.width * scale)), max(1, round(view.height * scale))))
    draw = ImageDraw.Draw(view)
    raw = (x1 - x0) / 8
    step = int(10 ** np.floor(np.log10(raw)) * min((1, 2, 5, 10), key=lambda m: abs(m * 10 ** np.floor(np.log10(raw)) - raw)))
    for gx in range((x0 // step + 1) * step, x1, step):
        px = (gx - x0) * scale
        draw.line([(px, 0), (px, view.height)], fill=(255, 0, 0), width=1)
        draw.text((px + 2, 2), str(gx), fill=(255, 0, 0))
    for gy in range((y0 // step + 1) * step, y1, step):
        py = (gy - y0) * scale
        draw.line([(0, py), (view.width, py)], fill=(255, 0, 0), width=1)
        draw.text((2, py + 2), str(gy), fill=(255, 0, 0))
    view.save(out)


def runs(flags: np.ndarray, min_len: int = 1) -> list[tuple[int, int]]:
    out, start = [], None
    for i, on in enumerate(np.append(flags, False)):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start >= min_len:
                out.append((start, i - 1))
            start = None
    return out


def line_angles(img: Image.Image, region, ink: int = 100, chunk: int = 40) -> list[dict]:
    """Fit each text line's baseline: ink centroid per column chunk, robust line fit.

    Each line gets the rows halfway to its neighbours, so the ends of a tilted
    line stay inside its band. Lines touching the region edge are skipped.
    """
    x0, y0, x1, y1 = region
    mask = np.asarray(img.convert("L").crop(region), dtype=np.int16) < ink
    found = runs(mask.sum(axis=1) > max(3, 0.01 * mask.shape[1]), min_len=8)
    lines = []
    for i, (top, bottom) in enumerate(found):
        if top == 0 or bottom == mask.shape[0] - 1:
            continue
        lo = (found[i - 1][1] + top) // 2 if i else 0
        hi = (bottom + found[i + 1][0]) // 2 if i + 1 < len(found) else mask.shape[0] - 1
        band = mask[lo : hi + 1]
        xs, ys = [], []
        for c in range(0, band.shape[1] - chunk + 1, chunk):
            hit = np.nonzero(band[:, c : c + chunk].any(axis=1))[0]
            if hit.size >= 4:
                xs.append(c + chunk / 2)
                ys.append(hit.mean())
        if len(xs) < 10:
            continue
        xs, ys = np.array(xs), np.array(ys)
        fit = np.polyfit(xs, ys, 1)
        resid = np.abs(ys - np.polyval(fit, xs))
        keep = resid <= max(2.0, 2 * np.median(resid))
        if keep.sum() < 10:
            continue
        fit = np.polyfit(xs[keep], ys[keep], 1)
        lines.append({"y": y0 + top, "points": int(keep.sum()),
                      "angle": round(float(np.degrees(np.arctan(fit[0]))), 3)})
    return lines


def refine_edges(img: Image.Image, rough, reach: int) -> dict:
    """Move each side of a rough box to the sharpest brightness step within +-reach px.

    Uses the median over the middle 60% of the side, so photos and text near
    one spot do not win. Returns the refined box and each side's step height.
    """
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    x0, y0, x1, y1 = rough
    h, w = y1 - y0, x1 - x0
    rows = slice(y0 + int(0.2 * h), y1 - int(0.2 * h))
    cols = slice(x0 + int(0.2 * w), x1 - int(0.2 * w))

    def step(profile: np.ndarray, start: int) -> tuple[int, float]:
        smooth = np.convolve(profile, np.ones(5) / 5, mode="same")
        grad = np.abs(np.diff(smooth))[3:-3]
        i = int(np.argmax(grad)) + 3
        return start + i + 1, round(float(grad[i - 3]), 1)

    def span(c: int, limit: int) -> tuple[int, int]:
        return max(0, c - reach), min(limit, c + reach)

    a, b = span(x0, gray.shape[1]); left = step(np.median(gray[rows, a:b], axis=0), a)
    a, b = span(x1, gray.shape[1]); right = step(np.median(gray[rows, a:b], axis=0), a)
    a, b = span(y0, gray.shape[0]); top = step(np.median(gray[a:b, cols], axis=1), a)
    a, b = span(y1, gray.shape[0]); bottom = step(np.median(gray[a:b, cols], axis=1), a)
    return {"box": f"{left[0]},{top[0]},{right[0]},{bottom[0]}",
            "step": {"left": left[1], "top": top[1], "right": right[1], "bottom": bottom[1]}}


def source_quality(src: Image.Image) -> int:
    """libjpeg quality the scan was saved at, from its luminance quantization table."""
    luma = src.quantization[0]
    scale = sum(luma) / len(luma) / 57.625 * 100  # 57.625 = mean of the standard table at quality 50
    quality = (200 - scale) / 2 if scale <= 100 else 5000 / scale
    return int(min(95, max(10, round(quality))))


def save_jpeg(out: Image.Image, dest: Path, source: Path, dpi: float) -> int:
    """Re-encode at the scan's own quality and chroma subsampling, never larger than the scan.

    A fixed high quality (e.g. 95, no subsampling) can quadruple a low-quality
    scan's size without adding detail; matching the source keeps the size in line.
    """
    with Image.open(source) as src:
        quality, subsampling = source_quality(src), max(JpegImagePlugin.get_sampling(src), 0)
    limit = source.stat().st_size
    while True:
        buf = io.BytesIO()
        out.save(buf, "JPEG", quality=quality, subsampling=subsampling, dpi=(dpi, dpi), optimize=True)
        if buf.tell() <= limit or quality <= 30:
            dest.write_bytes(buf.getvalue())
            return quality
        quality -= 5


def corner_previews(img: Image.Image, out: Path) -> None:
    side = max(200, min(img.size) // 8)
    sheet = Image.new("RGB", (710, 710), "red")
    corners = ((0, 0), (img.width - side, 0), (0, img.height - side), (img.width - side, img.height - side))
    for i, (x, y) in enumerate(corners):
        sheet.paste(img.crop((x, y, x + side, y + side)).resize((350, 350)), ((i % 2) * 360, (i // 2) * 360))
    sheet.save(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info")
    p.add_argument("image", type=Path)
    p = sub.add_parser("grid")
    p.add_argument("image", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--angle", type=float, default=0.0)
    p.add_argument("--region", type=parse_box, help="x0,y0,x1,y1 in (rotated) full-res pixels")
    p = sub.add_parser("angle")
    p.add_argument("image", type=Path)
    p.add_argument("--region", type=parse_box, required=True, help="x0,y0,x1,y1 around long printed lines")
    p.add_argument("--angle", type=float, default=0.0, help="pre-rotate first (to verify a result)")
    p.add_argument("--ink", type=int, default=100, help="gray level below which a pixel is ink")
    p = sub.add_parser("edges")
    p.add_argument("image", type=Path)
    p.add_argument("--angle", type=float, required=True)
    p.add_argument("--rough", type=parse_box, required=True, help="x0,y0,x1,y1 read off a grid preview")
    p.add_argument("--reach", type=int, default=60, help="search +-px around each rough side")
    p = sub.add_parser("apply")
    p.add_argument("image", type=Path)
    p.add_argument("output", type=Path, help="output .jpg (never the input path)")
    p.add_argument("--angle", type=float, required=True)
    p.add_argument("--box", type=parse_box, required=True, help="x0,y0,x1,y1 on the rotated scan")
    p.add_argument("--dpi", type=float, help="only when the scan records none")
    p.add_argument("--preview-dir", type=Path, required=True)
    args = ap.parse_args()

    if args.cmd == "info":
        with Image.open(args.image) as img:
            print(json.dumps({"size": img.size, "dpi": source_dpi(args.image)}))
        return
    img = rotate(Image.open(args.image).convert("RGB"), args.angle)
    if args.cmd == "grid":
        grid_preview(img, args.out, args.region)
        print(args.out)
    elif args.cmd == "angle":
        lines = line_angles(img, args.region, args.ink)
        angles = [ln["angle"] for ln in lines]
        print(json.dumps({"lines": lines, "median": round(float(np.median(angles)), 3) if angles else None,
                          "spread": round(max(angles) - min(angles), 3) if angles else None}, indent=2))
    elif args.cmd == "edges":
        print(json.dumps(refine_edges(img, args.rough, args.reach)))
    else:
        if args.output.resolve() == args.image.resolve():
            raise SystemExit("refusing to overwrite the input scan")
        dpi = args.dpi or source_dpi(args.image)
        if not dpi:
            raise SystemExit("no DPI in the scan or a sibling PDF; ask the user, then pass --dpi")
        out = img.crop(args.box)
        quality = save_jpeg(out, args.output, args.image, dpi)
        args.preview_dir.mkdir(parents=True, exist_ok=True)
        whole = out.copy()
        whole.thumbnail((900, 900))
        whole.save(args.preview_dir / f"{args.output.stem}_whole.jpg")
        corner_previews(out, args.preview_dir / f"{args.output.stem}_corners.jpg")
        print(json.dumps({"output": str(args.output), "size_px": out.size, "dpi": dpi,
                          "size_in": [round(out.width / dpi, 3), round(out.height / dpi, 3)],
                          "quality": quality, "bytes": args.output.stat().st_size,
                          "source_bytes": args.image.stat().st_size,
                          "previews": str(args.preview_dir)}, indent=2))


if __name__ == "__main__":
    main()
