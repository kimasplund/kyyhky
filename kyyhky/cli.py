"""Command-line interface for Kyyhky."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from PIL import Image

from . import addresses as addr_mod
from . import layout as layout_mod
from . import media as media_mod
from . import protocol as proto
from .addresses import Address
from .layout import LayoutOptions
from .protocol import JobOptions, PrinterError

#: Printer address.  There is no sensible universal default for a LAN device,
#: so it must come from --host or the environment.
DEFAULT_HOST = os.environ.get("KYYHKY_HOST")
DEFAULT_PORT = int(os.environ.get("KYYHKY_PORT", proto.DEFAULT_PORT))

HOST_HELP = (
    "printer IP or hostname (or set KYYHKY_HOST)"
    if DEFAULT_HOST is None
    else f"printer IP or hostname (default {DEFAULT_HOST})"
)


def _require_host(a) -> str:
    if not getattr(a, "host", None):
        raise SystemExit(
            "no printer address. Pass --host 192.168.1.50 or set KYYHKY_HOST.\n"
            "Tip: 'kyyhky discover' scans your network for QL printers."
        )
    return a.host


# =========================================================================
# helpers
# =========================================================================
def _layout_opts(a) -> LayoutOptions:
    return LayoutOptions(
        font=a.font,
        pad_mm=a.pad,
        name_ratio=a.name_ratio,
        line_spacing=a.line_spacing,
        max_body=a.max_size,
        min_body=a.min_size,
        align=a.align,
        border=a.border,
        rotate=a.rotate,
        upper_country=not a.no_upper_country,
        valign=a.valign,
    )


def _resolve_cut(a) -> tuple[bool, int, int]:
    """Turn the cut flags into ``(auto_cut, cut_every, cut_at_end)``.

    Two independent bits drive the cutter -- ESC i M bit 6 (cut between
    labels) and ESC i K bit 3 (cut after the last label) -- so "no cutting"
    has to clear both, and "cut only at the end" is the combination of one
    off and the other on.
    """
    mode = getattr(a, "cut", "end")
    every = getattr(a, "cut_every", None)

    if getattr(a, "no_cut", False):
        mode = "never"
    elif every is not None:
        mode = "every"

    if mode == "never":
        auto_cut, cut_every, cut_at_end = False, 1, False
    elif mode == "each":
        auto_cut, cut_every, cut_at_end = True, 1, True
    elif mode == "every":
        n = max(1, min(255, every or 1))
        auto_cut, cut_every, cut_at_end = True, n, True
    else:  # "end" -- the default
        auto_cut, cut_every, cut_at_end = False, 1, True

    if getattr(a, "no_cut_at_end", False):
        cut_at_end = False
    return auto_cut, cut_every, cut_at_end


def _job_opts(a) -> JobOptions:
    auto_cut, cut_every, cut_at_end = _resolve_cut(a)
    return JobOptions(
        auto_cut=auto_cut,
        cut_every=cut_every,
        cut_at_end=cut_at_end,
        high_resolution=a.hires,
        compress=not a.no_compress,
        first_byte_is=a.first_byte,
        mirror=not a.no_mirror,
        feed_dots=a.feed,
    )


def _describe_cut(opts: JobOptions) -> str:
    if opts.auto_cut and opts.cut_every > 1:
        tail = " and at the end" if opts.cut_at_end else ""
        return f"after every {opts.cut_every} labels{tail}"
    if opts.auto_cut:
        return "after every label"
    if opts.cut_at_end:
        return "once at the end of the job"
    return "never (labels stay on the roll)"


def _resolve_media(a) -> media_mod.LabelSpec:
    try:
        return media_mod.get(a.media)
    except KeyError as exc:
        raise SystemExit(str(exc)) from exc


def _length_dots(a, spec) -> int | None:
    if spec.die_cut:
        return None
    if a.length is None:
        raise SystemExit(
            f"media {spec.key} is continuous tape; pass --length MM "
            "to say how long each label should be"
        )
    dots = media_mod.mm_to_dots(a.length)
    lo, hi = media_mod.CONTINUOUS_MIN_LENGTH_DOTS, media_mod.CONTINUOUS_MAX_LENGTH_DOTS
    if not lo <= dots <= hi:
        raise SystemExit(
            f"--length {a.length}mm is outside the printable range "
            f"({media_mod.dots_to_mm(lo):.1f}-{media_mod.dots_to_mm(hi):.0f} mm)"
        )
    return dots


def _collect(a) -> list[Address]:
    """Build the address list from --csv and/or inline --to flags."""
    out: list[Address] = []
    if a.csv:
        path = Path(a.csv)
        if not path.exists():
            raise SystemExit(f"no such file: {path}")
        try:
            out.extend(addr_mod.load(path))
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the user
            raise SystemExit(f"could not read {path}: {exc}") from exc
    if a.to:
        out.append(
            Address(
                name=a.to,
                att=a.att or "",
                street=a.street or "",
                number=a.number or "",
                apartment=a.apartment or "",
                postal=a.postal or "",
                city=a.city or "",
                country=a.country or "",
            )
        )
    if not out:
        raise SystemExit("nothing to print: pass --csv FILE or --to NAME ...")
    if a.limit:
        out = out[: a.limit]
    return addr_mod.expand_copies(out)


def _render_all(items, spec, lopts, length_dots, verbose=True):
    pages, warnings = [], []
    for i, address in enumerate(items, 1):
        img, info = layout_mod.render(address, spec, lopts, length_dots)
        pages.append(img)
        if info["overflow"]:
            warnings.append((i, address, info))
        if verbose:
            flag = "  <-- TIGHT" if info["overflow"] else ""
            print(
                f"  {i:>3}. {address.one_line()[:64]:<64} "
                f"{info['name_size']}/{info['body_size']}pt{flag}"
            )
    return pages, warnings


# =========================================================================
# commands
# =========================================================================
def cmd_discover(a) -> int:
    """Scan the local network for printers listening on port 9100."""
    import ipaddress

    if a.network:
        try:
            net = ipaddress.ip_network(a.network, strict=False)
        except ValueError as exc:
            raise SystemExit(f"bad --network: {exc}") from exc
    else:
        local = _local_ipv4()
        if not local:
            raise SystemExit(
                "could not determine this machine's IP; pass --network 192.168.1.0/24"
            )
        net = ipaddress.ip_network(f"{local}/24", strict=False)

    hosts = [str(h) for h in net.hosts()]
    print(f"Scanning {net} ({len(hosts)} addresses) on port {a.port} ...")

    # A cold ARP cache makes the first sweep miss hosts that are really there,
    # so sweep twice and merge.  The second pass is fast because the kernel has
    # the neighbour entries by then.
    found = _sweep(hosts, a.port, a.timeout)
    if a.passes > 1:
        for _ in range(a.passes - 1):
            found |= _sweep(hosts, a.port, a.timeout)

    if not found:
        print("No printers found.")
        print("  - is the printer powered on and on this network?")
        print("  - try a longer probe: --timeout 1.0")
        print("  - try another subnet:  --network 192.168.1.0/24")
        return 1

    print()
    ordered = sorted(found, key=lambda ip: tuple(int(p) for p in ip.split(".")))
    labelled = [(ip, _identify(ip)) for ip in ordered]
    for ip, name in labelled:
        print(f"  {ip:<16} {name}")

    # Prefer something that looks like a QL label printer.
    best = next((ip for ip, name in labelled if "QL-" in name), ordered[0])
    print()
    print("Use it with:")
    print(f"  export KYYHKY_HOST={best}")
    print(f"  kyyhky status --host {best}")
    return 0


def _sweep(hosts: list[str], port: int, timeout: float) -> set[str]:
    """One concurrent pass over the address list; returns what answered."""
    import concurrent.futures
    import socket

    def probe(ip: str) -> str | None:
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return ip
        except OSError:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        return {ip for ip in pool.map(probe, hosts) if ip}


def _local_ipv4() -> str | None:
    """This machine's LAN address, found without sending anything."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


#: "Brother QL-580N", "Brother HL-L2360D", "Brother MFC-J4335DW", ...
_MODEL_RE = re.compile(r"Brother\s+((?:[A-Z]{2,3})-[A-Z]?\d+[A-Z0-9]*)")


def _identify(ip: str, timeout: float = 6.0) -> str:
    """Best-effort model name from the printer's embedded web server.

    The QL-580N's 2007-era board is slow -- it can take three or four seconds
    to answer -- so this timeout is deliberately generous.
    """
    import urllib.error
    import urllib.request

    for path in ("/printer/main.html", "/"):
        try:
            with urllib.request.urlopen(f"http://{ip}{path}", timeout=timeout) as r:
                body = r.read(16384).decode("utf-8", "replace")
        except (urllib.error.URLError, OSError):
            continue
        m = _MODEL_RE.search(body)
        if m:
            return f"Brother {m.group(1)}"
        m = re.search(r"<title>([^<]{1,60})</title>", body, re.I)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return "(device on 9100, model unknown)"


def cmd_fonts(a) -> int:
    installed = layout_mod.available_fonts()
    if not installed:
        print("No known font families found. Pass --font /path/to/font.ttf,")
        print("or install DejaVu Sans (Debian/Ubuntu: fonts-dejavu-core).")
        return 1
    try:
        bold, regular = layout_mod.resolve_fonts()
    except FileNotFoundError:
        bold = regular = "?"
    print("Installed families:")
    for name in installed:
        b, r = layout_mod.FONT_FAMILIES[name]
        mark = " <-- default" if layout_mod._find(b) == bold else ""
        print(f"  {name}{mark}")
    print(f"\nAuto-selected bold   : {bold}")
    print(f"Auto-selected regular: {regular}")
    return 0


def cmd_media(a) -> int:
    print("Die-cut labels:")
    for s in media_mod.DIE_CUT:
        mark = " <-- default" if s.key == media_mod.DEFAULT_MEDIA else ""
        print(f"  {s.describe()}{mark}")
    print("\nContinuous tape (needs --length MM):")
    for s in media_mod.CONTINUOUS:
        print(f"  {s.describe()}")
    return 0


def cmd_status(a) -> int:
    host = _require_host(a)
    print(f"Printer   : {host}:{a.port}")
    try:
        status = proto.query_status(host, a.port, timeout=a.timeout)
    except PrinterError as exc:
        print(f"Result    : UNREACHABLE - {exc}")
        return 1
    if status is None:
        print("Result    : reachable, port 9100 accepting data")
        print(
            "Status    : not reported.  The QL-580N's Ethernet board defines no\n"
            "            status channel (Raster Command Reference 6.9), so this\n"
            "            is expected, not a fault.  Printing still works."
        )
        return 0
    print(f"Model     : {status['model']}")
    print(f"Media     : {status['media_width_mm']}mm x {status['media_length_mm']}mm "
          f"({status['media_type']})")
    print(f"State     : {status['status_type']}")
    print(f"Errors    : {', '.join(status['errors']) or 'none'}")
    return 0


def cmd_preview(a) -> int:
    spec = _resolve_media(a)
    lopts = _layout_opts(a)
    length_dots = _length_dots(a, spec)
    items = _collect(a)

    print(f"Media  : {spec.describe()}")
    print(f"Labels : {len(items)}")
    pages, warnings = _render_all(items, spec, lopts, length_dots)

    out = Path(a.out)
    if len(pages) == 1:
        layout_mod.preview(pages[0], scale=a.scale).save(out)
        print(f"\nWrote {out}")
    else:
        cards = [layout_mod.preview(p, scale=a.scale) for p in pages]
        w = max(c.width for c in cards)
        h = sum(c.height for c in cards)
        sheet = Image.new("L", (w, h), 210)
        y = 0
        for c in cards:
            sheet.paste(c, (0, y))
            y += c.height
        sheet.save(out)
        print(f"\nWrote {out} ({len(pages)} labels stacked)")

    if warnings:
        print(f"\n{len(warnings)} label(s) hit the minimum font size:")
        for i, address, _ in warnings[:5]:
            print(f"  {i}. {address.one_line()[:70]}")
    return 0


def cmd_print(a) -> int:
    spec = _resolve_media(a)
    lopts = _layout_opts(a)
    jopts = _job_opts(a)
    length_dots = _length_dots(a, spec)
    items = _collect(a)
    host = a.host if a.dry_run else _require_host(a)

    print(f"Printer: {host or '(dry run)'}:{a.port}")
    print(f"Media  : {spec.describe()}")
    print(f"Labels : {len(items)}")
    print(f"Cut    : {_describe_cut(jopts)}")
    pages, warnings = _render_all(items, spec, lopts, length_dots)

    if warnings and not a.yes:
        print(f"\n{len(warnings)} label(s) are at the minimum font size.")

    raster_pages = []
    for img in pages:
        printer_img = layout_mod.to_printer_space(img, lopts.rotate)
        raster_pages.append(proto.image_to_raster_lines(printer_img, spec, jopts))

    job = proto.build_job(raster_pages, spec, jopts)
    kib = len(job) / 1024
    print(f"\nJob    : {len(job)} bytes ({kib:.1f} KiB), "
          f"{sum(len(p) for p in raster_pages)} raster lines")

    if a.dry_run:
        print("Dry run: nothing sent.")
        if a.save_job:
            Path(a.save_job).write_bytes(job)
            print(f"Saved job to {a.save_job}")
        return 0

    if not a.yes:
        try:
            reply = input(f"Print {len(pages)} label(s)? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("Cancelled.")
            return 1

    try:
        sent = proto.send(job, host, a.port, timeout=a.timeout)
    except PrinterError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"Sent {sent} bytes to {host}:{a.port} - printing.")
    if a.save_job:
        Path(a.save_job).write_bytes(job)
        print(f"Saved job to {a.save_job}")
    return 0


def cmd_calibrate(a) -> int:
    """Print two probe labels to settle reading direction.

    Offset and mirroring are already pinned down by a hardware calibration
    run on Kim's QL-580N:

      * byte 0 of a raster line is the RIGHT margin (section 3.2.5 -- the
        'left' variant printed a blank label, i.e. content fell off the media)
      * the pin axis runs right-to-left, so it must be reversed, otherwise
        the output is mirrored

    What remains is which way round the label feeds, so this prints the same
    address rotated each way.  Keep whichever reads correctly.
    """
    spec = _resolve_media(a)
    lopts = _layout_opts(a)
    lopts.border = True
    jopts = _job_opts(a)
    length_dots = _length_dots(a, spec)

    raster_pages = []
    for rot in ("cw", "ccw"):
        probe = Address(
            name=f"ROTATE {rot.upper()}",
            att="if this reads correctly,",
            street=f"use  --rotate {rot}",
            postal="00250",
            city="Helsinki",
            country="Finland",
        )
        img, _ = layout_mod.render(probe, spec, lopts, length_dots)
        printer_img = layout_mod.to_printer_space(img, rot)
        raster_pages.append(proto.image_to_raster_lines(printer_img, spec, jopts))

    job = proto.build_job(raster_pages, spec, jopts)
    host = a.host if a.dry_run else _require_host(a)
    print(f"Calibration: 2 labels ({len(job)} bytes) -> {host or '(dry run)'}:{a.port}")
    print("  Each label names the --rotate value that produced it.")
    print("  Keep whichever one reads the right way round.")
    if a.dry_run:
        print("Dry run: nothing sent.")
        return 0
    try:
        proto.send(job, host, a.port, timeout=a.timeout)
    except PrinterError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print("Sent.")
    return 0


def cmd_sample(a) -> int:
    path = Path(a.out)
    path.write_text(
        "name,att,street,number,apartment,postal,city,country,copies\n"
        "Ada Lovelace,,Wilton Place,12,,SW1X 8RL,London,United Kingdom,1\n"
        "Example Oy,Purchasing,Mannerheimintie,140,A 3,00250,Helsinki,Finland,2\n"
        "Grace Hopper,,Arlington Ridge,7,,22204,Arlington,United States,1\n"
        "Björn Söderström,,Storgatan,7,,90210,Umeå,Sweden,1\n",
        encoding="utf-8",
    )
    print(f"Wrote {path}")
    print(f"Try:  kyyhky preview --csv {path} --out labels.png")
    return 0


# =========================================================================
# argument parsing
# =========================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kyyhky",
        description="Address labels for the Brother QL-580N over the network.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  kyyhky discover                       # find the printer on your LAN\n"
            "  export KYYHKY_HOST=192.168.1.50\n"
            "  kyyhky sample --out addr.csv\n"
            "  kyyhky preview --csv addr.csv --out labels.png\n"
            "  kyyhky print --csv addr.csv --yes\n"
            "  kyyhky print --csv addr.csv --cut-every 10 --yes\n"
            "  kyyhky print --to 'Ada Lovelace' --street 'Wilton Place' --number 12 \\\n"
            "               --postal 'SW1X 8RL' --city London --country 'United Kingdom'\n"
        ),
    )
    p.add_argument("--version", action="version", version="kyyhky 1.0.0")
    sub = p.add_subparsers(dest="command", required=True)

    def add_conn(sp):
        sp.add_argument("--host", default=DEFAULT_HOST, help=HOST_HELP)
        sp.add_argument("--port", type=int, default=DEFAULT_PORT)
        sp.add_argument("--timeout", type=float, default=20.0)

    def add_media(sp):
        sp.add_argument("--media", default=media_mod.DEFAULT_MEDIA,
                        help=f"label size (default {media_mod.DEFAULT_MEDIA})")
        sp.add_argument("--length", type=float, default=None,
                        help="label length in mm (continuous tape only)")

    def add_source(sp):
        sp.add_argument("--csv", help="CSV/TSV/JSON/JSONL address file")
        sp.add_argument("--to", help="single recipient name")
        sp.add_argument("--att", help="attention / c-o line")
        sp.add_argument("--street")
        sp.add_argument("--number")
        sp.add_argument("--apartment", "--apt", dest="apartment")
        sp.add_argument("--postal")
        sp.add_argument("--city")
        sp.add_argument("--country")
        sp.add_argument("--limit", type=int, help="only the first N records")

    def add_layout(sp):
        sp.add_argument("--font", help="dejavu | inter | liberation | noto | condensed | PATH")
        sp.add_argument("--pad", type=float, default=1.6, help="inner margin mm")
        sp.add_argument("--name-ratio", type=float, default=1.32)
        sp.add_argument("--line-spacing", type=float, default=1.16)
        sp.add_argument("--max-size", type=int, default=60)
        sp.add_argument("--min-size", type=int, default=16)
        sp.add_argument("--align", choices=["left", "center"], default="left")
        sp.add_argument("--valign", choices=["top", "middle", "bottom"], default="middle")
        sp.add_argument("--border", action="store_true", help="hairline around the label")
        sp.add_argument("--rotate", choices=["cw", "ccw"], default="cw")
        sp.add_argument("--no-upper-country", action="store_true")

    def add_job(sp):
        sp.add_argument(
            "--cut", choices=["end", "each", "every", "never"], default="end",
            help="when to cut: end (default, one cut after the whole run), "
                 "each label, every N (with --cut-every), or never",
        )
        sp.add_argument("--cut-every", type=int, default=None, metavar="N",
                        help="cut after every N labels (implies --cut every)")
        sp.add_argument("--no-cut", action="store_true",
                        help="shorthand for --cut never")
        sp.add_argument("--no-cut-at-end", action="store_true",
                        help="suppress the final cut, whatever --cut says")
        sp.add_argument("--hires", action="store_true", help="600 dpi lengthwise")
        sp.add_argument("--no-compress", action="store_true",
                        help="disable TIFF compression (breaks LAN printing)")
        sp.add_argument("--first-byte", choices=["right", "left"], default="right",
                        help="which margin byte 0 holds (hardware-confirmed: right)")
        sp.add_argument("--no-mirror", action="store_true",
                        help="do not reverse the pin axis (prints mirrored)")
        sp.add_argument("--feed", type=int, default=None, help="feed dots, continuous only")

    sp = sub.add_parser("discover", help="scan the LAN for label printers")
    sp.add_argument("--network", help="CIDR to scan, e.g. 192.168.1.0/24")
    sp.add_argument("--port", type=int, default=DEFAULT_PORT)
    sp.add_argument("--timeout", type=float, default=0.4,
                    help="per-host connect timeout (default 0.4)")
    sp.add_argument("--passes", type=int, default=2,
                    help="sweeps to run; >1 defeats a cold ARP cache (default 2)")
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser("media", help="list supported label sizes")
    sp.set_defaults(func=cmd_media)

    sp = sub.add_parser("fonts", help="list usable font families")
    sp.set_defaults(func=cmd_fonts)

    sp = sub.add_parser("status", help="check the printer is reachable")
    add_conn(sp)
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("sample", help="write an example CSV")
    sp.add_argument("--out", default="addresses.csv")
    sp.set_defaults(func=cmd_sample)

    sp = sub.add_parser("preview", help="render to PNG without printing")
    add_media(sp); add_source(sp); add_layout(sp)
    sp.add_argument("--out", default="preview.png")
    sp.add_argument("--scale", type=int, default=1)
    sp.set_defaults(func=cmd_preview)

    sp = sub.add_parser("print", help="render and print")
    add_conn(sp); add_media(sp); add_source(sp); add_layout(sp); add_job(sp)
    sp.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--save-job", help="also write the raw job bytes here")
    sp.set_defaults(func=cmd_print)

    sp = sub.add_parser("calibrate", help="print cw/ccw test labels")
    add_conn(sp); add_media(sp); add_layout(sp); add_job(sp)
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_calibrate)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
