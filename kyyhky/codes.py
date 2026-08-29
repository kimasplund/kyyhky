"""Bar codes and QR codes rendered at exact printer-dot resolution.

Both generators here work from the raw module matrix rather than asking a
library for a PNG and scaling it.  That matters on a 300 dpi thermal head:
a scaled bitmap lands module edges on fractional dots, the printer rounds
them, and bar widths come out uneven.  Uneven bars are what a scanner
refuses to read.

Rendering from the matrix at an integer number of dots per module makes
every bar and every QR cell exactly the same width, which is the single
biggest factor in whether a small printed code scans reliably.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from . import media

#: Bar code symbologies, mapped to the python-barcode class name.
SYMBOLOGIES: dict[str, str] = {
    "code128": "code128",
    "code39": "code39",
    "codabar": "codabar",
    "ean13": "ean13",
    "ean8": "ean8",
    "ean14": "ean14",
    "upca": "upca",
    "isbn13": "isbn13",
    "isbn10": "isbn10",
    "issn": "issn",
    "itf": "itf",
    "gs1_128": "gs1_128",
    "pzn": "pzn",
}

#: Friendly aliases people actually type.
SYMBOLOGY_ALIASES: dict[str, str] = {
    "code-128": "code128",
    "c128": "code128",
    "128": "code128",
    "code-39": "code39",
    "c39": "code39",
    "39": "code39",
    "3of9": "code39",
    "nw-7": "codabar",
    "nw7": "codabar",
    "ean": "ean13",
    "ean-13": "ean13",
    "ean-8": "ean8",
    "upc": "upca",
    "upc-a": "upca",
    "gs1": "gs1_128",
    "gs1-128": "gs1_128",
    "i2of5": "itf",
    "interleaved2of5": "itf",
}

#: QR error-correction levels: recoverable fraction of the symbol.
QR_ECC = {
    "l": "~7%",
    "m": "~15%",
    "q": "~25%",
    "h": "~30%",
}

#: Quiet zone required either side of a 1-D bar code, in modules.
BARCODE_QUIET_MODULES = 10

#: Quiet zone required around a QR symbol, in modules (ISO/IEC 18004).
QR_QUIET_MODULES = 4


class CodeError(ValueError):
    """Raised when data cannot be encoded in the requested symbology."""


def resolve_symbology(name: str) -> str:
    """Normalise a user-supplied symbology name."""
    key = name.strip().lower().replace(" ", "").replace("_", "_")
    key = SYMBOLOGY_ALIASES.get(key, key)
    if key in SYMBOLOGIES:
        return key
    raise CodeError(
        f"unknown symbology {name!r}. Known: "
        f"{', '.join(sorted(SYMBOLOGIES))}"
    )


# --- QR -------------------------------------------------------------------


def qr_matrix(
    data: str,
    ecc: str = "m",
    micro: bool = False,
) -> list[list[int]]:
    """Return the QR symbol as a list of rows of 0/1, without quiet zone."""
    try:
        import segno
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise CodeError(
            "QR codes need the 'segno' package: pip install \"kyyhky[qr]\""
        ) from exc

    level = ecc.strip().lower()[:1]
    if level not in QR_ECC:
        raise CodeError(
            f"QR error correction must be one of {', '.join(QR_ECC)}, "
            f"got {ecc!r}"
        )
    try:
        sym = segno.make(data, error=level, micro=micro)
    except Exception as exc:  # segno raises several distinct types
        raise CodeError(f"cannot encode {data!r} as QR: {exc}") from exc
    return [list(row) for row in sym.matrix]


def qr_image(
    data: str,
    module_dots: int = 4,
    ecc: str = "m",
    micro: bool = False,
    quiet: int = QR_QUIET_MODULES,
) -> Image.Image:
    """Render a QR code, one module = ``module_dots`` printer dots square.

    The quiet zone is included in the returned image, because a QR code
    printed without one frequently will not scan.
    """
    module_dots = max(1, int(module_dots))
    quiet = max(0, int(quiet))
    matrix = qr_matrix(data, ecc=ecc, micro=micro)

    modules = len(matrix)
    side = (modules + 2 * quiet) * module_dots
    img = Image.new("1", (side, side), 1)  # 1 = white
    px = img.load()

    for r, row in enumerate(matrix):
        y0 = (r + quiet) * module_dots
        for c, on in enumerate(row):
            if not on:
                continue
            x0 = (c + quiet) * module_dots
            for dy in range(module_dots):
                for dx in range(module_dots):
                    px[x0 + dx, y0 + dy] = 0  # 0 = black
    return img


def qr_fit_module(target_dots: int, data: str, ecc: str = "m",
                  micro: bool = False, quiet: int = QR_QUIET_MODULES) -> int:
    """Largest whole module size whose symbol still fits ``target_dots``."""
    modules = len(qr_matrix(data, ecc=ecc, micro=micro)) + 2 * max(0, quiet)
    return max(1, int(target_dots) // modules)


# --- 1-D bar codes --------------------------------------------------------


def _make(data: str, symbology: str, checksum: bool = False):
    """Instantiate a python-barcode object, without writer machinery."""
    try:
        import barcode as _barcode
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise CodeError(
            "bar codes need the 'python-barcode' package: "
            'pip install "kyyhky[barcode]"'
        ) from exc

    key = resolve_symbology(symbology)
    cls = _barcode.get_barcode_class(SYMBOLOGIES[key])
    kwargs = {}
    # Code 39 defaults to appending a checksum character, which surprises
    # people: "ABC123" prints as "ABC123$".  Most label work does not want
    # it, so it is opt-in here.
    if key == "code39":
        kwargs["add_checksum"] = bool(checksum)
    try:
        return key, cls(data, writer=None, **kwargs)
    except Exception as exc:
        raise CodeError(f"cannot encode {data!r} as {key}: {exc}") from exc


def barcode_modules(data: str, symbology: str = "code128",
                    checksum: bool = False) -> str:
    """Return the bar pattern as a string of '1' (bar) and '0' (space)."""
    key, obj = _make(data, symbology, checksum)
    try:
        segments = obj.build()
    except Exception as exc:
        raise CodeError(f"cannot encode {data!r} as {key}: {exc}") from exc
    if not segments:
        raise CodeError(f"{key} produced no output for {data!r}")
    return segments[0]


def barcode_text(data: str, symbology: str = "code128",
                 checksum: bool = False) -> str:
    """The human-readable string for the code, including any check digit."""
    try:
        _key, obj = _make(data, symbology, checksum)
    except CodeError:
        return data
    # EAN/UPC classes expose the full code including the check digit.
    for attr in ("get_fullcode", "ean", "upc", "code"):
        value = getattr(obj, attr, None)
        if callable(value):
            try:
                return str(value())
            except Exception:
                continue
        elif isinstance(value, str):
            return value
    return data


def barcode_image(
    data: str,
    symbology: str = "code128",
    module_dots: int = 2,
    height_dots: int = 90,
    quiet: int = BARCODE_QUIET_MODULES,
    text: bool = True,
    font_path: str | None = None,
    text_dots: int = 24,
    text_gap_dots: int = 4,
    checksum: bool = False,
) -> Image.Image:
    """Render a 1-D bar code with every bar an exact multiple of one dot.

    ``module_dots`` is the narrow-bar width.  Two dots at 300 dpi is about
    0.17 mm, which most scanners handle comfortably; one dot is possible but
    unforgiving of thermal bleed.
    """
    module_dots = max(1, int(module_dots))
    height_dots = max(1, int(height_dots))
    quiet = max(0, int(quiet))

    pattern = barcode_modules(data, symbology, checksum)
    bars_w = len(pattern) * module_dots
    width = bars_w + 2 * quiet * module_dots

    caption = barcode_text(data, symbology, checksum) if text else ""
    font = None
    cap_h = 0
    if caption:
        font = _load_font(font_path, text_dots)
        if font is not None:
            probe = ImageDraw.Draw(Image.new("1", (8, 8), 1))
            box = probe.textbbox((0, 0), caption, font=font)
            cap_h = (box[3] - box[1]) + text_gap_dots
        else:
            caption = ""

    img = Image.new("1", (width, height_dots + cap_h), 1)
    draw = ImageDraw.Draw(img)

    x = quiet * module_dots
    for ch in pattern:
        if ch == "1":
            draw.rectangle(
                [x, 0, x + module_dots - 1, height_dots - 1], fill=0
            )
        x += module_dots

    if caption and font is not None:
        box = draw.textbbox((0, 0), caption, font=font)
        tw = box[2] - box[0]
        draw.text(
            ((width - tw) / 2 - box[0], height_dots + text_gap_dots - box[1]),
            caption,
            font=font,
            fill=0,
        )
    return img


def barcode_fit_module(target_dots: int, data: str,
                       symbology: str = "code128",
                       quiet: int = BARCODE_QUIET_MODULES) -> int:
    """Largest whole narrow-bar width whose code still fits ``target_dots``."""
    modules = len(barcode_modules(data, symbology)) + 2 * max(0, quiet)
    return max(1, int(target_dots) // modules)


def _load_font(path: str | None, size: int):
    from .layout import resolve_fonts

    try:
        if path:
            return ImageFont.truetype(path, max(1, int(size)))
        _bold, regular = resolve_fonts(None)
        return ImageFont.truetype(regular, max(1, int(size)))
    except (OSError, FileNotFoundError):
        return None


# --- Introspection --------------------------------------------------------


@dataclass(frozen=True)
class SymbologyInfo:
    key: str
    accepts: str
    note: str


SYMBOLOGY_NOTES: list[SymbologyInfo] = [
    SymbologyInfo("code128", "any ASCII", "densest general choice; the default"),
    SymbologyInfo("code39", "A-Z 0-9 - . $ / + % space", "widely readable, low density"),
    SymbologyInfo("codabar", "0-9 - $ : / . +", "libraries, blood banks"),
    SymbologyInfo("ean13", "12 or 13 digits", "retail; check digit added"),
    SymbologyInfo("ean8", "7 or 8 digits", "small retail packs"),
    SymbologyInfo("ean14", "13 or 14 digits", "shipping containers"),
    SymbologyInfo("upca", "11 or 12 digits", "North American retail"),
    SymbologyInfo("isbn13", "ISBN-13", "books"),
    SymbologyInfo("isbn10", "ISBN-10", "books, legacy"),
    SymbologyInfo("issn", "ISSN", "periodicals"),
    SymbologyInfo("itf", "even digit count", "interleaved 2 of 5, cartons"),
    SymbologyInfo("gs1_128", "GS1 element strings", "logistics"),
    SymbologyInfo("pzn", "6 or 7 digits", "German pharmaceutical"),
]


def describe_symbologies() -> list[str]:
    out = []
    for info in SYMBOLOGY_NOTES:
        out.append(f"{info.key:<10} {info.accepts:<26} {info.note}")
    return out


def mm(value: float) -> int:
    """Millimetres to whole printer dots."""
    return media.mm_to_dots(value)
