"""Decode a built job back into a bitmap -- the software check before printing.

Two views are produced:

``as_sent``    the raster exactly as the bytes describe it.
``as_printed`` the same raster with the pin axis reversed, which models what
               the QL-580N physically puts on the label.

``as_printed`` models the pin-axis reversal that the printer applies, so it is
the view that should read correctly.  Printing without that reversal produced
mirrored text on real hardware.
"""

import argparse
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kyyhky import layout, media, protocol  # noqa: E402
from kyyhky.addresses import Address  # noqa: E402
from kyyhky.protocol import JobOptions  # noqa: E402
from tests.test_kyyhky import unpackbits  # noqa: E402


def decode(job: bytes) -> list[bytes]:
    """Walk a job stream and rebuild its raster lines."""
    i = job.index(b"\x4d\x02") + 2
    rows: list[bytes] = []
    while i < len(job):
        c = job[i]
        if c == 0x5A:  # Z -- zero raster
            rows.append(b"\x00" * media.RASTER_BYTES)
            i += 1
        elif c == 0x67:  # g -- raster transfer
            n = job[i + 2]
            rows.append(unpackbits(job[i + 3 : i + 3 + n]))
            i += 3 + n
        elif c == 0x1B and job[i : i + 3] == b"\x1b\x69\x7a":
            i += 13  # next page header
        else:
            i += 1
    return rows


def to_image(rows: list[bytes]) -> Image.Image:
    """Rebuild the full 720-pin raster exactly as the bytes describe it."""
    img = Image.new("1", (media.PINS, len(rows)), 1)
    px = img.load()
    for y, row in enumerate(rows):
        for x in range(media.PINS):
            if row[x >> 3] & (0x80 >> (x & 7)):
                px[x, y] = 0
    return img


def ink_window(rows: list[bytes]) -> tuple[int, int] | None:
    lo, hi = media.PINS, -1
    for row in rows:
        for x in range(media.PINS):
            if row[x >> 3] & (0x80 >> (x & 7)):
                lo = min(lo, x)
                hi = max(hi, x)
    return (lo, hi) if hi >= 0 else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--media", default="29x90")
    ap.add_argument("--rotate", default="cw", choices=["cw", "ccw"])
    ap.add_argument("--no-mirror", action="store_true")
    args = ap.parse_args()

    spec = media.get(args.media)
    opts = JobOptions(mirror=not args.no_mirror)
    a = Address(
        name="Ada Lovelace", street="Wilton Place", number="12", apartment="5",
        postal="00250", city="Helsinki", country="Finland",
    )
    img, _ = layout.render(a, spec)
    pimg = layout.to_printer_space(img, args.rotate)
    job = protocol.build_job(
        [protocol.image_to_raster_lines(pimg, spec, opts)], spec, opts
    )

    rows = decode(job)
    print(f"decoded {len(rows)} raster lines x {len(rows[0])} bytes")
    assert len(rows) == spec.print_l, f"{len(rows)} != {spec.print_l}"

    win = ink_window(rows)
    live = (spec.right_pins, spec.right_pins + spec.print_w - 1)
    print(f"ink occupies pins {win}, live print window is {live}")
    if win and (win[0] < live[0] or win[1] > live[1]):
        print("  *** INK OUTSIDE THE WINDOW -- this would print blank/clipped ***")
    else:
        print("  ink is inside the printable window")

    back = Image.Transpose.ROTATE_90 if args.rotate == "cw" else Image.Transpose.ROTATE_270
    full = to_image(rows)
    # Crop to the pins that sit over the label FIRST.  The remaining ~414 pins
    # are head overhanging 29 mm media.  Reversing the pin axis before cropping
    # would move the window to the opposite end and yield a blank picture --
    # which is exactly what an earlier version of this tool did.
    window = full.crop((live[0], 0, live[1] + 1, full.height))
    for name, rev in (("as_sent", False), ("as_printed", True)):
        out = window.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if rev else window
        out.transpose(back).save(f"/tmp/decoded_{name}.png")
        print(f"wrote /tmp/decoded_{name}.png  ({spec.width_mm}x{spec.length_mm}mm label)")


if __name__ == "__main__":
    main()
