"""Render an :class:`~kyyhky.addresses.Address` into a print-ready bitmap.

Everything happens in *landscape* label space first (long edge horizontal,
text reading normally), because that is what a human wants to look at in a
preview.  :func:`to_printer_space` performs the final rotation into the
orientation the print head expects.
"""

from __future__ import annotations

import functools
import glob
import os
import sys
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from . import media
from .addresses import Address
from .media import LabelSpec

# Directories worth searching for TrueType/OpenType faces, by platform.
if sys.platform == "darwin":
    FONT_ROOTS = [
        "/System/Library/Fonts",
        "/Library/Fonts",
        os.path.expanduser("~/Library/Fonts"),
    ]
elif os.name == "nt":
    FONT_ROOTS = [
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
        os.path.expanduser(r"~\AppData\Local\Microsoft\Windows\Fonts"),
    ]
else:
    FONT_ROOTS = [
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.local/share/fonts"),
        os.path.expanduser("~/.fonts"),
    ]

#: Font families we know how to use, best first.  Each entry is
#: ``(bold_filenames, regular_filenames)``.  DejaVu leads because it has
#: complete coverage of Nordic and Baltic diacritics (Å Ä Ö Õ Ü Ø Æ).
FONT_FAMILIES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "dejavu": (("DejaVuSans-Bold.ttf",), ("DejaVuSans.ttf",)),
    "condensed": (
        ("DejaVuSansCondensed-Bold.ttf",),
        ("DejaVuSansCondensed.ttf",),
    ),
    "liberation": (
        ("LiberationSans-Bold.ttf",),
        ("LiberationSans-Regular.ttf",),
    ),
    "dejavu-sans-mono": (("DejaVuSansMono-Bold.ttf",), ("DejaVuSansMono.ttf",)),
    "noto": (
        ("NotoSans-Bold.ttf", "NotoSans-Bold.otf"),
        ("NotoSans-Regular.ttf", "NotoSans-Regular.otf"),
    ),
    "inter": (("Inter-Bold.otf", "Inter-Bold.ttf"), ("Inter-Regular.otf", "Inter-Regular.ttf")),
    "roboto": (("Roboto-Bold.ttf",), ("Roboto-Regular.ttf",)),
    "arial": (("Arial Bold.ttf", "arialbd.ttf"), ("Arial.ttf", "arial.ttf")),
    "helvetica": (("Helvetica-Bold.ttf", "HelveticaNeue.ttc"), ("Helvetica.ttc", "Helvetica.ttf")),
    "freesans": (("FreeSansBold.ttf",), ("FreeSans.ttf",)),
}

#: Order in which families are tried when the caller does not name one.
FONT_PREFERENCE = [
    "dejavu",
    "liberation",
    "noto",
    "inter",
    "roboto",
    "freesans",
    "helvetica",
    "arial",
]


@functools.lru_cache(maxsize=1)
def _font_index() -> dict[str, str]:
    """Map lower-cased font filename -> absolute path, scanning known roots."""
    index: dict[str, str] = {}
    for root in FONT_ROOTS:
        if not os.path.isdir(root):
            continue
        for ext in ("ttf", "otf", "ttc"):
            for path in glob.iglob(
                os.path.join(root, "**", f"*.{ext}"), recursive=True
            ):
                index.setdefault(os.path.basename(path).lower(), path)
    return index


def _find(names: tuple[str, ...]) -> str | None:
    index = _font_index()
    for name in names:
        hit = index.get(name.lower())
        if hit:
            return hit
    return None


def _fc_match(pattern: str) -> str | None:
    """Ask fontconfig, when it is available (Linux/BSD)."""
    import shutil
    import subprocess

    if not shutil.which("fc-match"):
        return None
    try:
        out = subprocess.run(
            ["fc-match", "-f", "%{file}", pattern],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    path = out.stdout.strip()
    return path if path and os.path.exists(path) else None


def available_fonts() -> list[str]:
    """Family names that are actually installed on this machine."""
    out = []
    for name, (bold, regular) in FONT_FAMILIES.items():
        if _find(bold) and _find(regular):
            out.append(name)
    return out


def resolve_fonts(name: str | None = None) -> tuple[str, str]:
    """Return ``(bold_path, regular_path)`` for a family name, or auto-pick.

    ``name`` may be a known family, or a path to a font file (used for both
    weights).  With no name, the first installed family from
    :data:`FONT_PREFERENCE` wins, falling back to fontconfig.
    """
    if name:
        if os.path.exists(name):  # explicit path -> use for both weights
            return name, name
        key = name.strip().lower()
        if key in FONT_FAMILIES:
            bold, regular = FONT_FAMILIES[key]
            bold_path, regular_path = _find(bold), _find(regular)
            if bold_path and regular_path:
                return bold_path, regular_path
            installed = ", ".join(available_fonts()) or "none found"
            raise FileNotFoundError(
                f"font family {name!r} is not installed. Available: {installed}"
            )
        raise FileNotFoundError(
            f"unknown font {name!r} -- pass a family "
            f"({', '.join(sorted(FONT_FAMILIES))}) or a path to a .ttf/.otf"
        )

    for family in FONT_PREFERENCE:
        bold, regular = FONT_FAMILIES[family]
        bold_path, regular_path = _find(bold), _find(regular)
        if bold_path and regular_path:
            return bold_path, regular_path

    bold_path = _fc_match("sans-serif:bold")
    regular_path = _fc_match("sans-serif")
    if bold_path and regular_path:
        return bold_path, regular_path

    raise FileNotFoundError(
        "no usable TrueType font found. Install one (e.g. DejaVu Sans) or "
        "pass --font /path/to/font.ttf"
    )


@dataclass
class LayoutOptions:
    """Typography and geometry knobs."""

    font: str | None = None
    #: Inner padding in millimetres (top/bottom, left/right).
    pad_mm: float = 1.6
    #: Name size relative to body size.
    name_ratio: float = 1.32
    #: Multiple of the font size used as line advance.
    line_spacing: float = 1.16
    #: Extra gap under the name, as a multiple of the body size.
    name_gap: float = 0.30
    #: Largest body size we will ever use, in dots.
    max_body: int = 60
    #: Smallest legible body size, in dots.
    min_body: int = 16
    align: str = "left"  # left | center
    #: Draw a hairline around the printable area (calibration aid).
    border: bool = False
    #: Rotation applied when converting to printer space.
    rotate: str = "cw"  # cw | ccw
    upper_country: bool = True
    #: Vertical placement inside the label: top | middle | bottom
    valign: str = "middle"


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, max(1, int(size)))


def _measure(draw, text, font) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def fit_lines(
    lines: list[tuple[str, str]],
    area_w: int,
    area_h: int,
    opts: LayoutOptions,
) -> tuple[int, int, list]:
    """Pick the largest body size at which every line fits the print area.

    Returns ``(body_size, name_size, laid_out)`` where ``laid_out`` is a list
    of ``(text, font, width, height)``.
    """
    bold_path, regular_path = resolve_fonts(opts.font)
    probe = ImageDraw.Draw(Image.new("1", (8, 8), 1))

    for body in range(opts.max_body, opts.min_body - 1, -1):
        name_size = max(body + 1, int(round(body * opts.name_ratio)))
        laid: list = []
        total_h = 0.0
        widest = 0
        ok = True
        for idx, (style, text) in enumerate(lines):
            size = name_size if style == "name" else body
            font = _font(bold_path if style == "name" else regular_path, size)
            w, h = _measure(probe, text, font)
            if w > area_w:
                ok = False
                break
            widest = max(widest, w)
            laid.append((text, font, w, h))
            total_h += size * opts.line_spacing
            if style == "name" and idx + 1 < len(lines):
                total_h += body * opts.name_gap
        if ok and total_h <= area_h:
            return body, name_size, laid

    # Nothing fit even at min_body: return the smallest and let the caller
    # decide (the CLI warns about it).
    body = opts.min_body
    name_size = max(body + 1, int(round(body * opts.name_ratio)))
    laid = []
    for style, text in lines:
        size = name_size if style == "name" else body
        font = _font(bold_path if style == "name" else regular_path, size)
        w, h = _measure(probe, text, font)
        laid.append((text, font, w, h))
    return body, name_size, laid


def render(
    address: Address,
    spec: LabelSpec,
    opts: LayoutOptions | None = None,
    length_dots: int | None = None,
) -> tuple[Image.Image, dict]:
    """Render one address into a landscape 1-bit image of the print area.

    Returns ``(image, info)``; ``info`` reports the chosen font sizes and
    whether the text had to be shrunk below the comfortable minimum.
    """
    opts = opts or LayoutOptions()
    width = length_dots or spec.print_l
    if not width:
        raise ValueError("continuous media needs an explicit length_dots")
    height = spec.print_w

    img = Image.new("1", (width, height), 1)  # 1 = white
    draw = ImageDraw.Draw(img)

    pad = media.mm_to_dots(opts.pad_mm)
    area_w = width - 2 * pad
    area_h = height - 2 * pad

    body_lines = address.lines(upper_country=opts.upper_country)
    if not body_lines:
        body_lines = [("body", "(empty)")]

    body, name_size, laid = fit_lines(body_lines, area_w, area_h, opts)

    # Total block height for vertical alignment.
    total = 0.0
    for idx, (style, _) in enumerate(body_lines):
        size = name_size if style == "name" else body
        total += size * opts.line_spacing
        if style == "name" and idx + 1 < len(body_lines):
            total += body * opts.name_gap

    if opts.valign == "top":
        y = float(pad)
    elif opts.valign == "bottom":
        y = height - pad - total
    else:
        y = (height - total) / 2.0

    for idx, ((style, _), (text, font, w, _h)) in enumerate(zip(body_lines, laid)):
        size = name_size if style == "name" else body
        x = pad if opts.align == "left" else (width - w) / 2.0
        # Anchor "la" = left ascender: consistent baselines regardless of
        # whether a line happens to contain descenders or diacritics.
        draw.text((x, y), text, font=font, fill=0, anchor="la")
        y += size * opts.line_spacing
        if style == "name" and idx + 1 < len(body_lines):
            y += body * opts.name_gap

    if opts.border:
        draw.rectangle([0, 0, width - 1, height - 1], outline=0, width=2)

    overflow = any(w > area_w for _, _, w, _ in laid) or total > area_h
    info = {
        "body_size": body,
        "name_size": name_size,
        "overflow": overflow,
        "print_area": (width, height),
    }
    return img, info


def to_printer_space(img: Image.Image, rotate: str = "cw") -> Image.Image:
    """Rotate a landscape label image into print-head orientation.

    The head prints one raster line at a time across the media width, so the
    image handed to the protocol layer must be ``print_w`` wide and one row
    per raster line.  Which of the two 90-degree rotations is correct depends
    on how the media exits the machine -- ``kyyhky calibrate`` settles it.
    """
    if rotate == "cw":
        return img.transpose(Image.Transpose.ROTATE_270)  # clockwise
    if rotate == "ccw":
        return img.transpose(Image.Transpose.ROTATE_90)
    raise ValueError("rotate must be 'cw' or 'ccw'")


def preview(img: Image.Image, scale: int = 1, margin: int = 12) -> Image.Image:
    """Wrap a landscape label render in a grey card for on-screen viewing."""
    w, h = img.size
    canvas = Image.new("L", (w + 2 * margin, h + 2 * margin), 210)
    canvas.paste(img.convert("L"), (margin, margin))
    d = ImageDraw.Draw(canvas)
    d.rectangle(
        [margin - 1, margin - 1, margin + w, margin + h], outline=120, width=1
    )
    if scale > 1:
        canvas = canvas.resize(
            (canvas.width * scale, canvas.height * scale), Image.Resampling.LANCZOS
        )
    return canvas
