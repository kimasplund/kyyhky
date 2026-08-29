"""Custom label layouts: a declarative template plus a data row.

The address renderer in :mod:`kyyhky.layout` does one job well, but it only
knows how to draw an address.  This module is the general case: a template
lists elements -- text, bar codes, QR codes, images, lines, boxes -- each
with a position, and a data row fills in the placeholders.

Coordinates are **millimetres from the top-left of the label**, in landscape
orientation (the way you look at it), because that is what a person can
measure with a ruler.  Dots are available too (``x_dots``/``y_dots``) when
you want exact control.

    {
      "label": "62x29",
      "elements": [
        {"type": "text", "text": "{name}", "x": 3, "y": 3, "size": 4, "bold": true},
        {"type": "qr",   "data": "{url}",  "x": 45, "y": 3, "size": 20}
      ]
    }

Placeholders are ``{column}`` and are matched case-insensitively, ignoring
spaces and underscores, so ``{Product Name}``, ``{product_name}`` and
``{productname}`` all read the same CSV column.
"""

import csv
import io
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont

from . import codes, media
from .layout import resolve_fonts
from .media import LabelSpec

#: Element types a template may use.
ELEMENT_TYPES = (
    "text", "barcode", "qr", "image", "line", "box", "rule",
)

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


class TemplateError(ValueError):
    """Raised for malformed templates, with the offending element named."""


def _norm_key(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(name)).strip().lower()


def substitute(text: str, row: dict[str, Any]) -> str:
    """Replace ``{column}`` placeholders from ``row``.

    Unknown placeholders become empty strings rather than raising: a partly
    populated CSV should still print, with the missing bits simply absent.
    """
    if not text:
        return ""
    lookup = {_norm_key(k): ("" if v is None else str(v)) for k, v in row.items()}

    def repl(m: re.Match) -> str:
        return lookup.get(_norm_key(m.group(1)), "")

    return _PLACEHOLDER.sub(repl, str(text))


def placeholders(text: str) -> list[str]:
    """Column names referenced by a template string."""
    return [m.group(1) for m in _PLACEHOLDER.finditer(str(text or ""))]


@dataclass
class Element:
    """One drawable item in a template."""

    type: str
    spec: dict[str, Any] = field(default_factory=dict)
    index: int = 0

    def get(self, key: str, default=None):
        return self.spec.get(key, default)

    def dots(self, key: str, default_mm: float = 0.0) -> int:
        """Read a length in dots, accepting either ``key`` or ``key_dots``."""
        if f"{key}_dots" in self.spec:
            return int(self.spec[f"{key}_dots"])
        value = self.spec.get(key, default_mm)
        return media.mm_to_dots(float(value))

    def has(self, key: str) -> bool:
        return key in self.spec or f"{key}_dots" in self.spec


@dataclass
class Template:
    """A parsed label template."""

    label: str | None = None
    elements: list[Element] = field(default_factory=list)
    length_mm: float | None = None
    font: str | None = None
    background: str | None = None
    rotate: str = "cw"
    orientation: str = "auto"
    source: str | None = None

    @property
    def columns(self) -> list[str]:
        """Every distinct CSV column the template refers to."""
        seen: dict[str, None] = {}
        for el in self.elements:
            for key in ("text", "data", "path"):
                for name in placeholders(el.get(key, "")):
                    seen.setdefault(name, None)
        return list(seen)


def parse(obj: dict[str, Any], source: str | None = None) -> Template:
    """Validate a template dict into a :class:`Template`."""
    if not isinstance(obj, dict):
        raise TemplateError("template must be a JSON/YAML object")

    raw = obj.get("elements", obj.get("fields", []))
    if not isinstance(raw, list):
        raise TemplateError("'elements' must be a list")
    if not raw:
        raise TemplateError("template has no elements")

    elements: list[Element] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TemplateError(f"element {i}: must be an object")
        kind = str(item.get("type", "text")).strip().lower()
        if kind == "rule":
            kind = "line"
        if kind not in ELEMENT_TYPES:
            raise TemplateError(
                f"element {i}: unknown type {kind!r}. "
                f"Known: {', '.join(ELEMENT_TYPES)}"
            )
        spec = {k: v for k, v in item.items() if k != "type"}
        elements.append(Element(kind, spec, i))

    length = obj.get("length_mm", obj.get("length"))
    return Template(
        label=obj.get("label", obj.get("media")),
        elements=elements,
        length_mm=float(length) if length is not None else None,
        font=obj.get("font"),
        background=obj.get("background"),
        rotate=str(obj.get("rotate", "cw")),
        orientation=str(obj.get("orientation", "auto")).strip().lower(),
        source=source,
    )


def load(path: str) -> Template:
    """Read a template from a JSON or YAML file."""
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if path.lower().endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError as exc:
            raise TemplateError(
                'YAML templates need PyYAML: pip install "kyyhky[yaml]"'
            ) from exc
        obj = yaml.safe_load(text)
    else:
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TemplateError(f"{path}: invalid JSON: {exc}") from exc
    return parse(obj, source=path)


def loads(text: str) -> Template:
    """Parse a template from a JSON string."""
    try:
        return parse(json.loads(text))
    except json.JSONDecodeError as exc:
        raise TemplateError(f"invalid JSON: {exc}") from exc


# --- data -----------------------------------------------------------------


def read_rows(path: str) -> list[dict[str, Any]]:
    """Read CSV, TSV, JSON or JSONL into a list of dicts."""
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        text = fh.read()
    return read_rows_text(text, path)


def read_rows_text(text: str, name: str = "<data>") -> list[dict[str, Any]]:
    stripped = text.lstrip()
    if stripped.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise TemplateError(f"{name}: JSON data must be a list of objects")
        return [dict(r) for r in data]
    if stripped.startswith("{"):
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                rows.append(dict(json.loads(line)))
        return rows

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [dict(r) for r in csv.DictReader(io.StringIO(text), dialect=dialect)]


# --- rendering ------------------------------------------------------------


def canvas_size(
    template: Template,
    spec: LabelSpec,
    length_dots: int | None = None,
) -> tuple[int, int, bool]:
    """Work out the authoring canvas.

    Returns ``(width, height, rotated)``.  The printer's own canvas is
    ``print_l`` wide by ``print_w`` tall, which for media like 62x29 is a
    tall portrait strip -- not how anyone pictures a 62mm-wide asset tag.

    With ``orientation: "auto"`` (the default) a template is authored on a
    canvas whose long edge is horizontal, and rotated into printer space at
    the end.  ``"portrait"`` and ``"landscape"`` force the choice.
    """
    printer_w = length_dots or spec.print_l
    if not printer_w:
        if template.length_mm:
            printer_w = media.mm_to_dots(template.length_mm)
        else:
            raise TemplateError(
                "continuous media needs a length: set 'length_mm' in the "
                "template or pass --length"
            )
    printer_h = spec.print_w

    want = template.orientation
    if want in ("auto", ""):
        rotated = printer_h > printer_w
    elif want == "landscape":
        rotated = printer_h > printer_w
    elif want == "portrait":
        rotated = printer_w > printer_h
    else:
        raise TemplateError(
            f"orientation must be auto, landscape or portrait, "
            f"got {want!r}"
        )

    if rotated:
        return printer_h, printer_w, True
    return printer_w, printer_h, False


def render(
    template: Template,
    row: dict[str, Any] | None = None,
    spec: LabelSpec | None = None,
    length_dots: int | None = None,
) -> tuple[Image.Image, dict]:
    """Render one label from a template and a data row.

    Returns ``(image, info)``.  The image matches the printer's print area
    (``print_l`` x ``print_w``), ready for
    :func:`kyyhky.layout.to_printer_space`.
    """
    row = row or {}
    spec = spec or media.get(template.label or media.DEFAULT_MEDIA)

    width, height, rotated = canvas_size(template, spec, length_dots)

    img = Image.new("1", (width, height), 1)
    draw = ImageDraw.Draw(img)
    warnings: list[str] = []
    boxes: list[tuple[int, tuple[int, int, int, int]]] = []

    for el in template.elements:
        try:
            _draw(draw, img, el, row, template, width, height, warnings, boxes)
        except TemplateError:
            raise
        except codes.CodeError as exc:
            raise TemplateError(f"element {el.index} ({el.type}): {exc}") from exc
        except Exception as exc:
            raise TemplateError(
                f"element {el.index} ({el.type}): {exc}"
            ) from exc

    warnings.extend(_overlaps(boxes, template))

    if rotated:
        # Authored long-edge-horizontal; turn it into the printer's canvas.
        img = img.transpose(Image.Transpose.ROTATE_270)

    return img, {
        "print_area": img.size,
        "canvas": (width, height),
        "rotated": rotated,
        "elements": len(template.elements),
        "warnings": warnings,
    }


def _overlaps(
    boxes: list[tuple[int, tuple[int, int, int, int]]],
    template: Template,
) -> list[str]:
    """Report pairs of elements whose ink areas collide.

    Two elements can each sit comfortably inside the label and still print
    on top of each other -- a long name running into a price, say.  Bounds
    checking alone never catches that, so compare every pair.
    """
    out: list[str] = []
    for i in range(len(boxes)):
        idx_a, (ax1, ay1, ax2, ay2) = boxes[i]
        el_a = template.elements[idx_a] if idx_a < len(template.elements) else None
        if el_a is not None and el_a.get("overlap_ok"):
            continue
        for j in range(i + 1, len(boxes)):
            idx_b, (bx1, by1, bx2, by2) = boxes[j]
            el_b = template.elements[idx_b] if idx_b < len(template.elements) else None
            if el_b is not None and el_b.get("overlap_ok"):
                continue
            ox = min(ax2, bx2) - max(ax1, bx1)
            oy = min(ay2, by2) - max(ay1, by1)
            if ox > 1 and oy > 1:
                kind_a = el_a.type if el_a else "?"
                kind_b = el_b.type if el_b else "?"
                out.append(
                    f"elements {idx_a} ({kind_a}) and {idx_b} ({kind_b}) "
                    f"overlap by {ox}x{oy} dots"
                )
    return out


def _font_for(el: Element, template: Template, size_dots: int):
    name = el.get("font", template.font)
    bold_path, regular_path = resolve_fonts(name)
    path = bold_path if el.get("bold") else regular_path
    return ImageFont.truetype(path, max(1, int(size_dots)))


def _place(el: Element, w: int, h: int, iw: int, ih: int) -> tuple[int, int]:
    """Resolve an element's x/y, honouring align/valign and negative offsets."""
    align = str(el.get("align", "left")).lower()
    valign = str(el.get("valign", "top")).lower()

    if el.has("x"):
        x = el.dots("x")
        if x < 0:
            x = w + x - iw
    elif align == "center":
        x = (w - iw) // 2
    elif align == "right":
        x = w - iw
    else:
        x = 0

    if el.has("y"):
        y = el.dots("y")
        if y < 0:
            y = h + y - ih
    elif valign == "middle":
        y = (h - ih) // 2
    elif valign == "bottom":
        y = h - ih
    else:
        y = 0
    return int(x), int(y)


def _draw(draw, img, el: Element, row, template, w: int, h: int,
          warnings: list[str],
          boxes: list[tuple[int, tuple[int, int, int, int]]]) -> None:
    kind = el.type

    def record(x: int, y: int, iw: int, ih: int) -> None:
        boxes.append((el.index, (x, y, x + iw, y + ih)))
        if x < 0 or y < 0 or x + iw > w or y + ih > h:
            warnings.append(
                f"element {el.index} ({kind}) extends past the label"
            )

    if kind == "text":
        text = substitute(el.get("text", el.get("data", "")), row)
        if not text:
            return
        size = el.dots("size", 4.0)
        font = _font_for(el, template, size)
        box = draw.textbbox((0, 0), text, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]

        max_w = el.dots("max_width") if el.has("max_width") else None
        if max_w is None and el.has("x") and el.dots("x") < 0:
            # A right-anchored element with no explicit budget: keep it from
            # growing left across the whole label. Reserve the space between
            # the left edge and its own right-hand anchor.
            max_w = w + el.dots("x")
        if max_w and tw > max_w:
            # Shrink to fit rather than silently running off the label.
            for trial in range(int(size), 3, -1):
                font = _font_for(el, template, trial)
                box = draw.textbbox((0, 0), text, font=font)
                tw, th = box[2] - box[0], box[3] - box[1]
                if tw <= max_w:
                    break
        x, y = _place(el, w, h, tw, th)
        align = str(el.get("align", "left")).lower()
        # Align INSIDE an explicit max_width box, but only when x is a
        # left-hand anchor. A negative x is already right-anchored by
        # _place(), and nudging it again would push it off the label.
        if el.has("x") and el.dots("x") >= 0 and max_w and align in ("center", "right"):
            x = x + (max_w - tw) if align == "right" else x + (max_w - tw) // 2
        draw.text((x - box[0], y - box[1]), text, font=font, fill=0)
        record(x, y, tw, th)
        return

    if kind == "qr":
        data = substitute(el.get("data", el.get("text", "")), row)
        if not data:
            return
        ecc = str(el.get("ecc", "m"))
        micro = bool(el.get("micro", False))
        quiet = int(el.get("quiet", codes.QR_QUIET_MODULES))
        if el.has("module"):
            module = max(1, el.dots("module"))
        else:
            target = el.dots("size", 18.0)
            # A QR is square, so it is limited by whichever of the requested
            # size and the space actually left on the label is smaller --
            # otherwise a wide-but-short gap silently overflows the bottom.
            if el.has("y"):
                y_at = el.dots("y")
                if y_at >= 0:
                    target = min(target, h - y_at)
            module = codes.qr_fit_module(target, data, ecc, micro, quiet)
        code = codes.qr_image(data, module, ecc, micro, quiet)
        x, y = _place(el, w, h, code.width, code.height)
        img.paste(code, (x, y))
        record(x, y, code.width, code.height)
        return

    if kind == "barcode":
        data = substitute(el.get("data", el.get("text", "")), row)
        if not data:
            return
        sym = str(el.get("symbology", el.get("type_name", "code128")))
        height_dots = el.dots("height", 8.0)
        quiet = int(el.get("quiet", codes.BARCODE_QUIET_MODULES))
        show_text = bool(el.get("text_below", el.get("hri", True)))
        checksum = bool(el.get("checksum", False))
        if el.has("module"):
            module = max(1, el.dots("module"))
        else:
            target = el.dots("width", 40.0)
            module = codes.barcode_fit_module(target, data, sym, quiet)
        _bold, regular = resolve_fonts(el.get("font", template.font))
        code = codes.barcode_image(
            data, sym, module, height_dots, quiet, show_text,
            regular, el.dots("text_size", 2.5), max(1, el.dots("text_gap", 0.4)),
            checksum,
        )
        x, y = _place(el, w, h, code.width, code.height)
        img.paste(code, (x, y))
        record(x, y, code.width, code.height)
        return

    if kind == "image":
        path = substitute(el.get("path", el.get("data", "")), row)
        if not path:
            return
        if not os.path.exists(path):
            raise TemplateError(f"image not found: {path}")
        src = Image.open(path)
        if el.has("width") or el.has("height"):
            tw = el.dots("width") if el.has("width") else 0
            th = el.dots("height") if el.has("height") else 0
            if tw and not th:
                th = int(src.height * (tw / src.width))
            elif th and not tw:
                tw = int(src.width * (th / src.height))
            src = src.resize((max(1, tw), max(1, th)), Image.Resampling.LANCZOS)
        threshold = int(el.get("threshold", 128))
        mono = src.convert("L").point(lambda p: 255 if p > threshold else 0, "1")
        if el.get("invert"):
            mono = mono.point(lambda p: 0 if p else 1, "1")
        x, y = _place(el, w, h, mono.width, mono.height)
        img.paste(mono, (x, y))
        record(x, y, mono.width, mono.height)
        return

    if kind == "line":
        thickness = max(1, el.dots("thickness", 0.3))
        x = el.dots("x")
        y = el.dots("y")
        if el.has("length"):
            length = el.dots("length")
            vertical = bool(el.get("vertical"))
            x2 = x if vertical else x + length
            y2 = y + length if vertical else y
        else:
            x2 = el.dots("x2", media.dots_to_mm(w))
            y2 = el.dots("y2", media.dots_to_mm(y))
        draw.line([x, y, x2, y2], fill=0, width=thickness)
        return

    if kind == "box":
        x = el.dots("x")
        y = el.dots("y")
        bw = el.dots("width", 10.0)
        bh = el.dots("height", 10.0)
        thickness = max(1, el.dots("thickness", 0.3))
        if el.get("filled"):
            draw.rectangle([x, y, x + bw - 1, y + bh - 1], fill=0)
        else:
            draw.rectangle(
                [x, y, x + bw - 1, y + bh - 1], outline=0, width=thickness
            )
        return

    raise TemplateError(f"unhandled element type {kind!r}")


# --- built-in starting points --------------------------------------------


BUILTIN: dict[str, dict[str, Any]] = {
    "address": {
        "label": "29x90",
        "elements": [
            {"type": "text", "text": "{name}", "x": 3, "y": 3.5, "size": 4.4,
             "bold": True, "max_width": 84},
            {"type": "text", "text": "{street}", "x": 3, "y": 10, "size": 3.4,
             "max_width": 84},
            {"type": "text", "text": "{postcode} {city}", "x": 3, "y": 14.5,
             "size": 3.4, "max_width": 84},
            {"type": "text", "text": "{country}", "x": 3, "y": 19, "size": 3.4,
             "max_width": 84},
        ],
    },
    "shipping": {
        "label": "62x100",
        "elements": [
            {"type": "text", "text": "{name}", "x": 4, "y": 4, "size": 5,
             "bold": True, "max_width": 62},
            {"type": "text", "text": "{street}", "x": 4, "y": 11, "size": 3.6},
            {"type": "text", "text": "{postcode} {city}", "x": 4, "y": 15.6,
             "size": 3.6},
            {"type": "text", "text": "{country}", "x": 4, "y": 20.2,
             "size": 3.6, "bold": True},
            {"type": "line", "x": 4, "y": 26, "x2": 96, "thickness": 0.4},
            {"type": "barcode", "data": "{tracking}", "x": 4, "y": 28,
             "width": 60, "height": 9, "symbology": "code128"},
            {"type": "qr", "data": "{tracking}", "x": -4, "y": 28, "size": 16},
        ],
    },
    "asset": {
        "label": "62x29",
        "elements": [
            {"type": "text", "text": "{name}", "x": 3, "y": 2.5, "size": 4,
             "bold": True, "max_width": 38},
            {"type": "text", "text": "{id}", "x": 3, "y": 8, "size": 3},
            {"type": "barcode", "data": "{id}", "x": 3, "y": 12,
             "width": 38, "height": 7, "symbology": "code128",
             "text_size": 2.2},
            {"type": "qr", "data": "{id}", "x": -3, "y": 2.5, "size": 23},
        ],
    },
    "product": {
        "label": "62x29",
        "elements": [
            {"type": "text", "text": "{name}", "x": 3, "y": 2, "size": 4,
             "bold": True, "max_width": 27},
            {"type": "text", "text": "{price}", "x": -3, "y": 2, "size": 4.6,
             "bold": True, "max_width": 27, "align": "right"},
            {"type": "barcode", "data": "{ean}", "x": 3, "y": 10,
             "width": 45, "height": 10, "symbology": "ean13"},
        ],
    },
    "qr-only": {
        "label": "29x90",
        "elements": [
            {"type": "qr", "data": "{url}", "x": 3, "valign": "middle",
             "size": 22},
            {"type": "text", "text": "{name}", "x": 30, "y": 6, "size": 4,
             "bold": True, "max_width": 55},
            {"type": "text", "text": "{note}", "x": 30, "y": 12, "size": 3,
             "max_width": 55},
        ],
    },
    "name-badge": {
        "label": "62x100",
        "rotate": "cw",
        "elements": [
            {"type": "text", "text": "{name}", "align": "center", "y": 14,
             "size": 8, "bold": True},
            {"type": "text", "text": "{title}", "align": "center", "y": 26,
             "size": 4},
            {"type": "text", "text": "{org}", "align": "center", "y": 33,
             "size": 3.4},
            {"type": "qr", "data": "{url}", "align": "center", "y": 40,
             "size": 18},
        ],
    },
}


def builtin(name: str) -> Template:
    key = name.strip().lower()
    if key not in BUILTIN:
        raise TemplateError(
            f"unknown built-in template {name!r}. "
            f"Known: {', '.join(sorted(BUILTIN))}"
        )
    return parse(BUILTIN[key], source=f"<builtin:{key}>")


def builtin_json(name: str) -> str:
    key = name.strip().lower()
    if key not in BUILTIN:
        raise TemplateError(f"unknown built-in template {name!r}")
    return json.dumps(BUILTIN[key], indent=2)


def sample_row(template: Template) -> dict[str, str]:
    """Plausible demo values for every column a template mentions."""
    defaults = {
        "name": "Ada Lovelace",
        "street": "12 Wilton Place",
        "postcode": "SW1X 8RL",
        "city": "London",
        "country": "UNITED KINGDOM",
        "url": "https://example.com/a1",
        "id": "ASSET-0042",
        "tracking": "JJFI61230000012345",
        "ean": "400638133393",
        "price": "12,90 EUR",
        "title": "Analyst",
        "org": "Example Ltd",
        "note": "scan me",
    }
    row = {}
    for col in template.columns:
        row[col] = defaults.get(_norm_key(col), col.replace("_", " ").title())
    return row
