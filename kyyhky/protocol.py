"""Brother QL-580N raster protocol: PackBits, job assembly, TCP transport.

Implements the command set documented in Brother's
"QL-500/550/560/570/580N/650TD/700/1050/1060N Raster Command Reference".

Two facts about the QL-580N over Ethernet drive this whole module:

1. **TIFF (PackBits) compression is mandatory.**  Section 5, "Compression mode
   selection":  *"In case QL-580N/1060N, serial and LAN interface should set
   TIFF option."*  An uncompressed job over port 9100 will not print.

2. **Network printing is fire-and-forget.**  Section 6.9 ("Buffering Normal
   Flow for Network") shows no status channel at all: the host streams the job
   and the printer starts printing once a full page is buffered.  The status
   request (ESC i S) that works over USB/serial simply never answers on the
   LAN board, so we must not block waiting for it.
"""

import socket
import time
from dataclasses import dataclass

from . import media
from .media import LabelSpec

DEFAULT_PORT = 9100

# --- Control codes --------------------------------------------------------
CMD_INVALIDATE = b"\x00" * 200  # clear any half-finished job in the printer
CMD_INITIALIZE = b"\x1b\x40"  # ESC @
CMD_STATUS_REQUEST = b"\x1b\x69\x53"  # ESC i S
CMD_MODE_RASTER = b"\x1b\x69\x61\x01"  # ESC i a 1
CMD_COMPRESSION_TIFF = b"\x4d\x02"  # M 2
CMD_COMPRESSION_NONE = b"\x4d\x00"  # M 0
CMD_ZERO_RASTER = b"\x5a"  # Z
CMD_PRINT = b"\x0c"  # FF   (page, more to follow)
CMD_PRINT_FEED = b"\x1a"  # SUB  (final page, feeds + cuts)

# ESC i z valid-flag bits
PI_KIND = 0x02
PI_WIDTH = 0x04
PI_LENGTH = 0x08
PI_QUALITY = 0x40
PI_RECOVER = 0x80

# ESC i M bits
MODE_AUTO_CUT = 0x40

# ESC i K bits
EXPANDED_CUT_AT_END = 0x08
EXPANDED_HIGH_RESOLUTION = 0x40


# =========================================================================
# PackBits / TIFF compression
# =========================================================================
def packbits(data: bytes, max_len: int | None = None) -> bytes:
    """Compress one raster line with the PackBits variant Brother calls TIFF.

    Encoding (section 5, "Compression mode selection"):
      * A run of N identical bytes (2..128) -> ``257-N`` as an unsigned byte
        (i.e. a negative signed count), followed by the byte.
      * A literal run of N differing bytes (1..128) -> ``N-1``, then the bytes.

    Brother's own worked example is covered by ``test_packbits_spec_example``.

    If the result would exceed ``max_len`` the whole line is emitted as a
    single literal run, which is what the spec prescribes ("the data is
    treated as being all different ... 91 bytes, including the 1 byte
    specifying the data length").
    """
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        run = 1
        while i + run < n and data[i + run] == data[i] and run < 128:
            run += 1
        if run >= 2:
            out.append(257 - run)  # 2 -> 0xFF, 20 -> 0xED
            out.append(data[i])
            i += run
            continue
        start = i
        lit = 0
        while i < n and lit < 128:
            # Break out if a worthwhile run (>=3) starts here.
            if i + 2 < n and data[i] == data[i + 1] == data[i + 2]:
                break
            i += 1
            lit += 1
        out.append(lit - 1)
        out += data[start : start + lit]

    if max_len is not None and len(out) > max_len:
        # Fall back to one all-literal block.
        return bytes([len(data) - 1]) + data
    return bytes(out)


# =========================================================================
# Raster assembly
# =========================================================================
@dataclass
class JobOptions:
    """Knobs for one print job.

    Cutting is governed by two independent bits, and the defaults here mean
    "keep the run joined, then cut once at the end":

    ============  ==========  ===========  ============
    intent        auto_cut    cut_every    cut_at_end
    ============  ==========  ===========  ============
    end (default) False       -            True
    each label    True        1            True
    every N       True        N            True
    never         False       -            False
    ============  ==========  ===========  ============
    """

    #: ESC i M bit 6 -- cut *between* labels.  Off by default: a run of
    #: address labels is easier to handle as one strip.
    auto_cut: bool = False
    #: ESC i A -- with auto_cut on, cut after every N labels.
    cut_every: int = 1
    #: ESC i K bit 3 -- cut after the final label of the job.
    cut_at_end: bool = True
    high_resolution: bool = False  # 600 dpi along the feed direction
    priority_quality: bool = True
    compress: bool = True  # must stay True for LAN
    #: Which end of the raster line byte 0 represents.  Section 3.2.5 says the
    #: right margin, and a QL-580N calibration print confirmed it: offsetting
    #: by the left margin instead puts the content off the media (blank label).
    first_byte_is: str = "right"
    #: Direction the pin index runs across the media.  The section 3.2.5
    #: diagram runs the raster line from the right margin to the left margin,
    #: so pin index increases right-to-left -- the opposite of image x.
    #: Leaving this False prints a mirror image (confirmed on hardware).
    mirror: bool = True
    feed_dots: int | None = None  # continuous tape only


def pack_raster_line(row: bytes) -> bytes:
    """Pack a row of 720 booleans (as a bytes of 0/1) into 90 bytes, MSB first."""
    out = bytearray(media.RASTER_BYTES)
    for x, on in enumerate(row):
        if on:
            out[x >> 3] |= 0x80 >> (x & 7)
    return bytes(out)


def image_to_raster_lines(img, spec: LabelSpec, opts: JobOptions) -> list[bytes]:
    """Turn a 1-bit PIL image in *printer space* into 90-byte raster lines.

    ``img`` must already be rotated into printer space: width = print area
    dots across the media, height = number of raster lines.  Black pixels
    (value 0 in PIL mode "1") are printed.
    """
    from PIL import Image

    if img.mode != "1":
        img = img.convert("1")

    if img.width != spec.print_w:
        raise ValueError(
            f"printer-space image width {img.width} != print area {spec.print_w}"
        )

    if opts.mirror:
        # Reverse the pin axis.  The section 3.2.5 raster line runs from the
        # right margin toward the left margin, i.e. opposite to image x.
        #
        # This MUST be done to the image, not to the assembled 720-pin canvas:
        # the live print window is off-centre (for 29x90 it is pins 6..311), so
        # flipping the whole canvas would relocate the content to pins 408..713
        # and print a blank label.  Confirmed on hardware -- that exact mistake
        # produced two blank labels.
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    offset = spec.right_pins if opts.first_byte_is == "right" else spec.left_pins
    canvas = Image.new("1", (media.PINS, img.height), 1)  # 1 = white
    canvas.paste(img, (offset, 0))

    # In PIL mode "1", 0 = black = "print this dot".
    px = canvas.load()
    lines: list[bytes] = []
    for y in range(canvas.height):
        row = bytes(1 if px[x, y] == 0 else 0 for x in range(media.PINS))
        lines.append(pack_raster_line(row))
    return lines


def build_page(
    lines: list[bytes],
    spec: LabelSpec,
    opts: JobOptions,
    first_page: bool,
    last_page: bool,
) -> bytes:
    """Emit the control codes + raster data for a single label."""
    buf = bytearray()

    # 2. Print information command (ESC i z)
    flags = PI_KIND | PI_WIDTH | PI_RECOVER
    if spec.die_cut:
        flags |= PI_LENGTH
    if opts.priority_quality:
        flags |= PI_QUALITY
    n = len(lines)
    buf += bytes(
        [
            0x1B,
            0x69,
            0x7A,
            flags,
            spec.media_type,
            spec.width_mm,
            spec.length_mm if spec.die_cut else 0,
            n & 0xFF,
            (n >> 8) & 0xFF,
            (n >> 16) & 0xFF,
            (n >> 24) & 0xFF,
            0 if first_page else 1,
            0,
        ]
    )

    # 3. Set each mode (ESC i M) -- auto cut
    buf += bytes([0x1B, 0x69, 0x4D, MODE_AUTO_CUT if opts.auto_cut else 0x00])

    # 4. Cut every N labels (ESC i A)
    if opts.auto_cut:
        buf += bytes([0x1B, 0x69, 0x41, max(1, min(255, opts.cut_every))])

    # 5. Set expanded mode (ESC i K)
    expanded = 0
    if opts.cut_at_end:
        expanded |= EXPANDED_CUT_AT_END
    if opts.high_resolution:
        expanded |= EXPANDED_HIGH_RESOLUTION
    buf += bytes([0x1B, 0x69, 0x4B, expanded])

    # 6. Set margin / feed amount (ESC i d).  Die-cut media must use 0.
    if spec.die_cut:
        feed = 0
    else:
        feed = opts.feed_dots if opts.feed_dots is not None else media.CONTINUOUS_MIN_FEED_DOTS
        feed = max(media.CONTINUOUS_MIN_FEED_DOTS, min(media.CONTINUOUS_MAX_FEED_DOTS, feed))
    buf += bytes([0x1B, 0x69, 0x64, feed & 0xFF, (feed >> 8) & 0xFF])

    # 7. Compression mode (M).  Mandatory on the LAN interface.
    buf += CMD_COMPRESSION_TIFF if opts.compress else CMD_COMPRESSION_NONE

    blank = b"\x00" * media.RASTER_BYTES
    for line in lines:
        if opts.compress:
            if line == blank:
                buf += CMD_ZERO_RASTER
            else:
                payload = packbits(line, max_len=media.RASTER_BYTES)
                buf += bytes([0x67, 0x00, len(payload)]) + payload
        else:
            buf += bytes([0x67, 0x00, media.RASTER_BYTES]) + line

    buf += CMD_PRINT_FEED if last_page else CMD_PRINT
    return bytes(buf)


def build_job(pages: list[list[bytes]], spec: LabelSpec, opts: JobOptions) -> bytes:
    """Assemble a complete multi-page job, ready to stream to port 9100."""
    buf = bytearray()
    buf += CMD_INVALIDATE
    buf += CMD_INITIALIZE
    buf += CMD_MODE_RASTER
    for i, lines in enumerate(pages):
        buf += build_page(
            lines, spec, opts, first_page=(i == 0), last_page=(i == len(pages) - 1)
        )
    return bytes(buf)


# =========================================================================
# Transport
# =========================================================================
class PrinterError(RuntimeError):
    pass


def send(
    data: bytes,
    host: str,
    port: int = DEFAULT_PORT,
    timeout: float = 20.0,
    chunk: int = 4096,
    drain: float = 0.5,
) -> int:
    """Stream a job to the printer over raw TCP (JetDirect, port 9100).

    Returns the number of bytes written.  The QL-580N applies back-pressure by
    stalling the socket when its buffer fills (section 6.9), so a plain
    blocking ``sendall`` in modest chunks is exactly right.
    """
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise PrinterError(f"cannot reach {host}:{port} - {exc}") from exc
    try:
        sock.settimeout(timeout)
        sent = 0
        for i in range(0, len(data), chunk):
            part = data[i : i + chunk]
            sock.sendall(part)
            sent += len(part)
        # Give the board a moment to move the tail of the job out of the
        # socket buffer before we tear the connection down.
        time.sleep(drain)
        return sent
    except OSError as exc:
        raise PrinterError(f"send failed after {sent} bytes: {exc}") from exc
    finally:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()


MODEL_CODES = {
    0x4F: "QL-500/550",
    0x31: "QL-560",
    0x32: "QL-570",
    0x33: "QL-580N",
    0x51: "QL-650TD",
    0x35: "QL-700",
    0x50: "QL-1050",
    0x34: "QL-1060N",
}

ERROR_1 = {
    0x01: "no media",
    0x02: "end of media",
    0x04: "cutter jam",
    0x10: "printer in use",
    0x80: "fan fault",
}
ERROR_2 = {
    0x04: "transmission error",
    0x10: "cover opened while printing",
    0x40: "cannot feed",
    0x80: "system error",
}
STATUS_TYPES = {
    0x00: "reply to status request",
    0x01: "printing completed",
    0x02: "error occurred",
    0x05: "notification",
    0x06: "phase change",
}


def parse_status(raw: bytes) -> dict:
    """Decode the fixed 32-byte status block (section 4)."""
    if len(raw) < 32:
        raise ValueError(f"status must be 32 bytes, got {len(raw)}")
    errs = [v for k, v in ERROR_1.items() if raw[8] & k]
    errs += [v for k, v in ERROR_2.items() if raw[9] & k]
    return {
        "model": MODEL_CODES.get(raw[4], f"unknown (0x{raw[4]:02x})"),
        "errors": errs,
        "media_width_mm": raw[10],
        "media_length_mm": raw[17],
        "media_type": {
            0x00: "none",
            0x0A: "continuous",
            0x0B: "die-cut",
        }.get(raw[11], f"0x{raw[11]:02x}"),
        "status_type": STATUS_TYPES.get(raw[18], f"0x{raw[18]:02x}"),
        "phase": raw[19],
        "raw": raw.hex(" "),
    }


def query_status(host: str, port: int = DEFAULT_PORT, timeout: float = 6.0) -> dict | None:
    """Best-effort ESC i S status query.

    Returns ``None`` when the printer accepts the request but never replies,
    which is the normal, documented behaviour of the QL-580N's Ethernet board
    (section 6.9 defines no status path for network printing).  Callers should
    treat ``None`` as "reachable, but status is USB/serial-only".
    """
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise PrinterError(f"cannot reach {host}:{port} - {exc}") from exc
    try:
        sock.settimeout(timeout)
        sock.sendall(CMD_INVALIDATE + CMD_INITIALIZE + CMD_STATUS_REQUEST)
        buf = b""
        try:
            while len(buf) < 32:
                part = sock.recv(32 - len(buf))
                if not part:
                    break
                buf += part
        except socket.timeout:
            return None
        return parse_status(buf) if len(buf) >= 32 else None
    finally:
        sock.close()
