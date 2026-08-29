"""Show the cutter bits each --cut mode actually puts on the wire.

Two commands govern cutting and they are easy to get subtly wrong, so this
prints the decoded bytes for every mode side by side.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kyyhky import cli, media, protocol  # noqa: E402

MODES = [
    ([], "default"),
    (["--cut", "each"], "--cut each"),
    (["--cut-every", "10"], "--cut-every 10"),
    (["--no-cut"], "--no-cut"),
    (["--cut", "each", "--no-cut-at-end"], "--cut each --no-cut-at-end"),
]

spec = media.get("29x90")
blank = [b"\x00" * media.RASTER_BYTES] * 4

print(f"{'mode':<28} {'ESC i M':>8} {'ESC i K':>8} {'ESC i A':>8}   behaviour")
print("-" * 88)
for extra, label in MODES:
    args = cli.build_parser().parse_args(
        ["print", "--to", "X", "--city", "Helsinki", *extra]
    )
    opts = cli._job_opts(args)
    job = protocol.build_job([blank, blank], spec, opts)

    m = job[job.index(b"\x1b\x69\x4d") + 3]
    k = job[job.index(b"\x1b\x69\x4b") + 3]
    has_a = b"\x1b\x69\x41" in job
    a = job[job.index(b"\x1b\x69\x41") + 3] if has_a else None

    print(
        f"{label:<28} "
        f"{'0x%02x' % m:>8} "
        f"{'0x%02x' % k:>8} "
        f"{(str(a) if has_a else '-'):>8}   "
        f"{cli._describe_cut(opts)}"
    )

print()
print("ESC i M bit 6 (0x40) = cut between labels")
print("ESC i K bit 3 (0x08) = cut after the final label")
print("ESC i A              = cut interval, only emitted when auto cut is on")
