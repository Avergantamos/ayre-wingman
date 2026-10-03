"""HUD number readers: `read_number(image) -> float | None`.

WinOcrReader: Windows' built-in OCR (Windows.Media.Ocr through the winrt packages in
  requirements.txt, installed into dependencies/). CPU only, no model.
TemplateReader: zero-dependency fallback. Learns the HUD's digit glyphs from one calibration
  crop whose value the vision model read, then matches glyphs with numpy.
"""
from __future__ import annotations

import asyncio
import re
import sys
import threading
from typing import Optional

import numpy as np
from PIL import Image

def _core():
    """flight_core, loaded by file path (custom_skills loads main.py outside any package)."""
    key = "ayre_flight_flight_core"
    if key not in sys.modules:
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("flight_core.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


parse_number = _core().parse_number

GLYPH_W, GLYPH_H = 10, 14
MAX_PER_CHAR = 6


# ---------------------------------------------------------------- image helpers

def to_gray(image) -> np.ndarray:
    """Brightness as float 0..1, using the max channel so colored HUD text (cyan) stays bright."""
    if isinstance(image, Image.Image):
        arr = np.asarray(image.convert("RGB"), dtype=np.float32)
    else:
        arr = np.asarray(image, dtype=np.float32)
    if arr.ndim == 3:
        arr = arr[..., :3].max(axis=2)
    return arr / 255.0 if arr.max() > 1.0 else arr


def otsu(gray: np.ndarray) -> float:
    hist, edges = np.histogram(gray, bins=64, range=(0.0, 1.0))
    centers = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * centers)
    mean0 = m0 / np.maximum(w0, 1)
    mean1 = (m0[-1] - m0) / np.maximum(w1, 1)
    between = w0 * w1 * (mean0 - mean1) ** 2
    return float(centers[int(np.argmax(between))])


def text_mask(image) -> np.ndarray:
    """Binary mask of the text: Otsu split, the minority class is the text (works for bright or dark text)."""
    gray = to_gray(image)
    if gray.max() - gray.min() < 0.1:
        return np.zeros(gray.shape, bool)
    mask = gray > otsu(gray)
    return mask if mask.mean() <= 0.5 else ~mask


def segment(mask: np.ndarray) -> list:
    """Glyph boxes (x0, y0, x1, y1), left to right, split on empty columns."""
    cols = mask.any(axis=0)
    boxes, x = [], 0
    w = len(cols)
    while x < w:
        if not cols[x]:
            x += 1
            continue
        x0 = x
        while x < w and cols[x]:
            x += 1
        rows = np.where(mask[:, x0:x].any(axis=1))[0]
        if mask[:, x0:x].sum() >= 2:
            boxes.append((x0, int(rows[0]), x, int(rows[-1]) + 1))
    if not boxes:
        return []
    # drop specks that are far smaller than the text and not on the line (noise)
    height = max(b[3] - b[1] for b in boxes)
    bottom = max(b[3] for b in boxes)
    return [b for b in boxes if (b[3] - b[1]) >= 0.12 * height or abs(b[3] - bottom) <= 0.25 * height]


def features(mask: np.ndarray, box: tuple, line_h: int, line_bottom: int) -> dict:
    x0, y0, x1, y1 = box
    crop = Image.fromarray((mask[y0:y1, x0:x1] * 255).astype(np.uint8))
    bmp = np.asarray(crop.resize((GLYPH_W, GLYPH_H), Image.BILINEAR), dtype=np.float32) / 255.0
    h = max(line_h, 1)
    return {"bmp": bmp, "h": (y1 - y0) / h, "w": (x1 - x0) / h, "b": (line_bottom - y1) / h}


def glyph_features(image) -> list:
    mask = text_mask(image)
    boxes = segment(mask)
    if not boxes:
        return []
    line_h = max(b[3] - b[1] for b in boxes)
    line_bottom = max(b[3] for b in boxes)
    return [features(mask, b, line_h, line_bottom) for b in boxes]


def distance(a: dict, b: dict) -> float:
    return float(np.abs(a["bmp"] - b["bmp"]).mean()) + 0.5 * abs(a["h"] - b["h"]) \
        + 0.3 * abs(a["w"] - b["w"]) + 0.3 * abs(a["b"] - b["b"])


# ---------------------------------------------------------------- template reader

class TemplateReader:
    """Nearest-template glyph matcher. `templates` is {char: [feature, ...]} for this region;
    `pool` holds glyphs learned on other regions (same HUD font), used with a small penalty."""

    name = "template"

    def __init__(self, templates: Optional[dict] = None, pool: Optional[dict] = None, max_dist: float = 0.22):
        self.templates = templates if templates is not None else {}
        self.pool = pool or {}
        self.max_dist = max_dist

    def learn(self, image, text: str) -> bool:
        """Label the crop's glyphs with the text the vision model read. False if they do not line up."""
        glyphs = glyph_features(image)
        chars = [c for c in (text or "") if not c.isspace()]
        if not glyphs or not chars:
            return False
        if len(glyphs) != len(chars):
            numeric = re.match(r"[-+\d.,]+", "".join(chars))
            if not numeric or len(glyphs) < len(numeric.group(0)):
                return False
            chars = list(numeric.group(0))              # units on the right did not segment cleanly
            glyphs = glyphs[:len(chars)]
        for g, c in zip(glyphs, chars):
            bucket = self.templates.setdefault(c, [])
            bucket.append(g)
            del bucket[:-MAX_PER_CHAR]
        return True

    def _classify(self, g: dict) -> str:
        best, best_c = 1e9, "?"
        for source, penalty in ((self.templates, 0.0), (self.pool, 0.03)):
            for c, items in source.items():
                for t in items:
                    d = distance(g, t) + penalty
                    if d < best:
                        best, best_c = d, c
        return best_c if best <= self.max_dist else "?"

    def read_text(self, image) -> str:
        return "".join(self._classify(g) for g in glyph_features(image))

    def read_number(self, image) -> Optional[float]:
        text = self.read_text(image)
        if not text or re.search(r"[\d.,\-]\?|\?[\d.,]", text):
            return None                                  # unknown glyph inside the number
        return parse_number(text.replace("?", " "))


def templates_to_json(templates: dict) -> dict:
    return {c: [{"bmp": np.round(t["bmp"], 2).tolist(), "h": round(t["h"], 3), "w": round(t["w"], 3),
                 "b": round(t["b"], 3)} for t in items] for c, items in templates.items()}


def templates_from_json(data: dict) -> dict:
    return {c: [{"bmp": np.asarray(t["bmp"], dtype=np.float32), "h": t["h"], "w": t["w"], "b": t["b"]}
                for t in items] for c, items in (data or {}).items()}


# ---------------------------------------------------------------- Windows OCR

try:  # Windows only, installed into dependencies/ by the setup step
    from winrt.windows.globalization import Language  # type: ignore
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap  # type: ignore
    from winrt.windows.media.ocr import OcrEngine  # type: ignore
    from winrt.windows.storage.streams import DataWriter  # type: ignore
    HAVE_WINOCR = sys.platform == "win32"
except Exception:  # not Windows, or packages not installed
    HAVE_WINOCR = False


class WinOcrReader:
    """Windows.Media.Ocr on a small, upscaled, dark-on-light copy of the crop."""

    name = "winocr"
    _local = threading.local()

    def __init__(self):
        if not HAVE_WINOCR:
            raise RuntimeError("Windows OCR not available")
        try:
            self.engine = OcrEngine.try_create_from_language(Language("en-US"))
        except Exception:
            self.engine = None
        self.engine = self.engine or OcrEngine.try_create_from_user_profile_languages()
        if self.engine is None:
            raise RuntimeError("no OCR language installed")

    def _loop(self) -> asyncio.AbstractEventLoop:
        loop = getattr(self._local, "loop", None)
        if loop is None:                                 # one private loop per thread that reads
            loop = self._local.loop = asyncio.new_event_loop()
        return loop

    @staticmethod
    def prepare(image) -> np.ndarray:
        mask = text_mask(image)
        gray = np.where(mask, 0, 255).astype(np.uint8)   # black text on white
        img = Image.fromarray(gray)
        scale = max(1.0, 48.0 / max(img.height, 1))
        if scale > 1:
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.BILINEAR)
        canvas = Image.new("L", (img.width + 20, img.height + 20), 255)
        canvas.paste(img, (10, 10))
        return np.asarray(canvas)

    def read_text(self, image) -> str:
        gray = self.prepare(image)
        h, w = gray.shape
        bgra = np.dstack([gray, gray, gray, np.full_like(gray, 255)]).tobytes()
        writer = DataWriter()
        try:
            writer.write_bytes(bgra)
        except TypeError:
            writer.write_bytes(list(bgra))
        bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)

        async def run():
            return await self.engine.recognize_async(bitmap)

        result = self._loop().run_until_complete(run())
        return result.text if result else ""

    def read_number(self, image) -> Optional[float]:
        try:
            return parse_number(self.read_text(image))
        except Exception:
            return None


def best_backend() -> Optional[WinOcrReader]:
    """The preferred OCR reader if it works on this machine, else None (use templates)."""
    if not HAVE_WINOCR:
        return None
    try:
        return WinOcrReader()
    except Exception:
        return None
