"""Brother QL media (label) specifications.

All figures are taken verbatim from Brother's official
"QL-500/550/560/570/580N/650TD/700/1050/1060N Raster Command Reference"
(cv_qlseries_eng_raster_600.pdf), sections 3.2.2 and 3.2.5.

The QL-580N print head has 720 pins across the media width.  Every raster
line is therefore 90 bytes.  For any given media only a slice of those pins
sits over the label; the rest are "margin" pins that must be sent as blank.

    720 pins  =  right_pins + print_w + left_pins

Per the raster-line diagram in section 3.2.5 the FIRST byte of a raster line
holds the right-margin pins and the LAST byte holds the left-margin pins.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Total pins on the QL-580N print head (also QL-500/550/560/570/650TD/700).
PINS = 720

#: Bytes per raster line = PINS / 8.
RASTER_BYTES = PINS // 8

#: Native resolution of the print head, dots per inch.
DPI = 300

#: Dots per millimetre at 300 dpi.
DOTS_PER_MM = DPI / 25.4  # 11.811...

MEDIA_TYPE_CONTINUOUS = 0x0A
MEDIA_TYPE_DIE_CUT = 0x0B


@dataclass(frozen=True)
class LabelSpec:
    """Geometry of one supported media type."""

    key: str
    label: str
    die_cut: bool
    width_mm: int
    length_mm: int  # 0 for continuous tape (length is caller-defined)
    print_w: int  # printable dots across the media width
    print_l: int  # printable dots along the feed direction (0 = variable)
    left_pins: int
    right_pins: int

    @property
    def media_type(self) -> int:
        return MEDIA_TYPE_DIE_CUT if self.die_cut else MEDIA_TYPE_CONTINUOUS

    @property
    def is_round(self) -> bool:
        return "dia" in self.key

    def validate(self) -> None:
        total = self.left_pins + self.print_w + self.right_pins
        if total != PINS:
            raise ValueError(
                f"{self.key}: pin budget {total} != {PINS} "
                f"({self.left_pins}+{self.print_w}+{self.right_pins})"
            )

    def describe(self) -> str:
        kind = "die-cut" if self.die_cut else "continuous"
        size = (
            f"{self.width_mm}mm x {self.length_mm}mm"
            if self.die_cut
            else f"{self.width_mm}mm continuous"
        )
        area = f"{self.print_w} x {self.print_l or '?'} dots"
        return f"{self.key:<12} {size:<22} {kind:<11} print area {area}"


def _spec(*args, **kwargs) -> LabelSpec:
    s = LabelSpec(*args, **kwargs)
    s.validate()
    return s


# --- Die-cut labels -------------------------------------------------------
# print_l values come from column 4 ("print area length") of section 3.2.2.
DIE_CUT = [
    _spec("17x54", "17mm x 54mm", True, 17, 54, 165, 566, 555, 0),
    _spec("17x87", "17mm x 87mm", True, 17, 87, 165, 956, 555, 0),
    _spec("23x23", "23mm x 23mm", True, 23, 23, 236, 202, 442, 42),
    _spec("29x90", "29mm x 90mm", True, 29, 90, 306, 991, 408, 6),
    _spec("38x90", "38mm x 90mm", True, 38, 90, 413, 991, 295, 12),
    _spec("39x48", "39mm x 48mm", True, 39, 48, 425, 495, 289, 6),
    _spec("52x29", "52mm x 29mm", True, 52, 29, 578, 271, 142, 0),
    _spec("62x29", "62mm x 29mm", True, 62, 29, 696, 271, 12, 12),
    _spec("62x100", "62mm x 100mm", True, 62, 100, 696, 1109, 12, 12),
    _spec("12dia", "12mm round", True, 12, 12, 94, 94, 513, 113),
    _spec("24dia", "24mm round", True, 24, 24, 236, 236, 442, 42),
    _spec("58dia", "58mm round", True, 58, 58, 618, 618, 51, 51),
]

# --- Continuous length tape ----------------------------------------------
CONTINUOUS = [
    _spec("12", "12mm continuous", False, 12, 0, 106, 0, 585, 29),
    _spec("29", "29mm continuous", False, 29, 0, 306, 0, 408, 6),
    _spec("38", "38mm continuous", False, 38, 0, 413, 0, 295, 12),
    _spec("50", "50mm continuous", False, 50, 0, 554, 0, 154, 12),
    _spec("54", "54mm continuous", False, 54, 0, 590, 0, 130, 0),
    _spec("62", "62mm continuous", False, 62, 0, 696, 0, 12, 12),
]

ALL: dict[str, LabelSpec] = {s.key: s for s in DIE_CUT + CONTINUOUS}

#: What is loaded in Kim's QL-580N right now.
DEFAULT_MEDIA = "29x90"

# Continuous tape feed limits, section 3.2.3/3.2.4 (QL-580N).
CONTINUOUS_MIN_FEED_DOTS = 35  # 3 mm
CONTINUOUS_MAX_FEED_DOTS = 1500  # 127 mm
CONTINUOUS_MIN_LENGTH_DOTS = 150  # 12.7 mm on QL-570/580N/700
CONTINUOUS_MAX_LENGTH_DOTS = 11811  # 1000 mm


def get(key: str) -> LabelSpec:
    """Look up a media spec, tolerating common spellings."""
    k = key.strip().lower().replace(" ", "").replace("mm", "")
    k = k.replace("*", "x").replace("×", "x").replace("_", "x")
    if k in ALL:
        return ALL[k]
    # "90x29" -> "29x90"
    if "x" in k:
        a, _, b = k.partition("x")
        if f"{b}x{a}" in ALL:
            return ALL[f"{b}x{a}"]
    raise KeyError(
        f"Unknown media {key!r}. Known: {', '.join(sorted(ALL))}"
    )


def mm_to_dots(mm: float) -> int:
    return int(round(mm * DOTS_PER_MM))


def dots_to_mm(dots: int) -> float:
    return dots / DOTS_PER_MM
