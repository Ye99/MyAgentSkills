from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deskew_crop.py"
TILT = 0.8  # degrees counterclockwise applied to the synthetic scan


def run(*args: str) -> str:
    return subprocess.run([sys.executable, str(SCRIPT), *args], check=True, capture_output=True, text=True).stdout


def make_scan(path: Path) -> None:
    """A tinted 'document' with printed lines on a grey scanner bed, tilted, at 300 DPI."""
    doc = Image.new("RGB", (1500, 1000), (210, 220, 240))
    draw = ImageDraw.Draw(doc)
    for row in range(8):
        y = 120 + row * 100
        for col in range(60):
            x = 80 + col * 22
            draw.rectangle((x, y, x + 14, y + 30), fill=(20, 20, 20))
    bed = Image.new("RGB", (2400, 1800), (228, 228, 228))
    bed.paste(doc, (400, 350))
    bed.rotate(TILT, resample=Image.BICUBIC, fillcolor=(228, 228, 228)).save(path, quality=85, dpi=(300, 300))


def test_measures_tilt_from_text_lines(tmp_path: Path) -> None:
    scan = tmp_path / "scan.jpg"
    make_scan(scan)

    result = json.loads(run("angle", str(scan), "--region", "500,450,1800,1250"))

    assert len(result["lines"]) >= 5
    assert abs(result["median"] + TILT) < 0.1
    assert result["spread"] < 0.15


def test_apply_keeps_dpi_and_stays_within_source_size(tmp_path: Path) -> None:
    scan = tmp_path / "scan.jpg"
    out = tmp_path / "scan_cropped.jpg"
    make_scan(scan)
    box = json.loads(run("edges", str(scan), "--angle", str(-TILT), "--rough", "390,340,1910,1360"))["box"]

    result = json.loads(run("apply", str(scan), str(out), "--angle", str(-TILT), "--box", box,
                            "--preview-dir", str(tmp_path / "pv")))

    x0, y0, x1, y1 = map(int, box.split(","))
    assert abs((x1 - x0) - 1500) <= 6 and abs((y1 - y0) - 1000) <= 6
    assert Image.open(out).info["dpi"] == (300, 300)
    assert result["bytes"] <= scan.stat().st_size
    assert not list(tmp_path.glob("*.pdf"))


def test_apply_refuses_to_overwrite_source(tmp_path: Path) -> None:
    scan = tmp_path / "scan.jpg"
    make_scan(scan)

    proc = subprocess.run([sys.executable, str(SCRIPT), "apply", str(scan), str(scan), "--angle", "0",
                           "--box", "0,0,10,10", "--preview-dir", str(tmp_path)], capture_output=True, text=True)

    assert proc.returncode != 0 and "overwrite" in proc.stderr
