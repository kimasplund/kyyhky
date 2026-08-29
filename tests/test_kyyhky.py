"""Tests for Kyyhky.  Run:  python -m pytest tests/ -v"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kyyhky import addresses, layout, media, protocol  # noqa: E402
from kyyhky.addresses import Address  # noqa: E402
from kyyhky.protocol import JobOptions, packbits  # noqa: E402


# =========================================================================
# PackBits -- validated against Brother's own documented example
# =========================================================================
def test_packbits_spec_example():
    """Brother Raster Command Reference, section 5, compression example.

    Input   : 00 x20, 22 x2, then 23 BA BF A2 22 2B
    Expected: ED 00  FF 22  05 23 BA BF A2 22 2B
    """
    raw = bytes([0x00] * 20 + [0x22] * 2 + [0x23, 0xBA, 0xBF, 0xA2, 0x22, 0x2B])
    expected = bytes(
        [0xED, 0x00, 0xFF, 0x22, 0x05, 0x23, 0xBA, 0xBF, 0xA2, 0x22, 0x2B]
    )
    assert packbits(raw) == expected


def unpackbits(data: bytes) -> bytes:
    """Reference PackBits decoder, used to prove round-trip fidelity."""
    out = bytearray()
    i = 0
    while i < len(data):
        n = data[i]
        i += 1
        if n < 128:  # literal run of n+1 bytes
            out += data[i : i + n + 1]
            i += n + 1
        else:  # repeat next byte 257-n times
            out += bytes([data[i]]) * (257 - n)
            i += 1
    return bytes(out)


@pytest.mark.parametrize(
    "raw",
    [
        b"\x00" * 90,
        b"\xff" * 90,
        bytes(range(90)),
        bytes([0x00] * 45 + [0xFF] * 45),
        bytes([0xAA, 0xAA, 0xAA] + [0x01, 0x02, 0x03] * 29),
        bytes([i % 7 for i in range(90)]),
    ],
)
def test_packbits_roundtrip(raw):
    assert unpackbits(packbits(raw)) == raw


def test_packbits_incompressible_falls_back_to_literal():
    raw = bytes((i * 37 + 11) % 256 for i in range(90))
    out = packbits(raw, max_len=media.RASTER_BYTES)
    assert unpackbits(out) == raw
    assert len(out) == 91  # 1 length byte + 90 data bytes


def test_packbits_long_run_splits_at_128():
    raw = b"\x5a" * 300
    assert unpackbits(packbits(raw)) == raw


# =========================================================================
# Media geometry -- every spec must account for all 720 pins
# =========================================================================
def test_every_media_uses_exactly_720_pins():
    for spec in media.ALL.values():
        assert spec.left_pins + spec.print_w + spec.right_pins == media.PINS, spec.key


def test_29x90_matches_the_manual():
    s = media.get("29x90")
    assert (s.width_mm, s.length_mm) == (29, 90)
    assert (s.print_w, s.print_l) == (306, 991)
    assert (s.left_pins, s.right_pins) == (408, 6)
    assert s.die_cut and s.media_type == media.MEDIA_TYPE_DIE_CUT


def test_media_lookup_is_forgiving():
    for name in ("29x90", "29X90", "29 x 90", "90x29", "29x90mm", "29×90"):
        assert media.get(name).key == "29x90"
    with pytest.raises(KeyError):
        media.get("nope")


def test_raster_bytes_is_90():
    assert media.RASTER_BYTES == 90


# =========================================================================
# Address parsing
# =========================================================================
def test_column_aliases_english_finnish_swedish():
    a = Address.from_mapping(
        {"Nimi": "Ada", "Katuosoite": "Wilton Place", "Nro": "12",
         "Postinumero": "00250", "Postitoimipaikka": "Helsinki", "Maa": "Finland"}
    )
    assert a.name == "Ada"
    assert a.street_line == "Wilton Place 12"
    assert a.city_line == "00250 Helsinki"


def test_apartment_gets_a_qualifier_only_when_bare():
    assert Address(street="Wilton Place", number="12", apartment="5").street_line == \
        "Wilton Place 12 as 5"
    assert Address(street="Wilton Place", number="12", apartment="A 3").street_line == \
        "Wilton Place 12 A 3"
    assert Address(street="Wilton Place", number="12", apartment="apt 9").street_line == \
        "Wilton Place 12 apt 9"


def test_att_prefix_not_doubled():
    assert Address(att="Ada").att_line == "att: Ada"
    assert Address(att="c/o Ada").att_line == "c/o Ada"
    assert Address(att="ATT: Ada").att_line == "ATT: Ada"


def test_lines_drop_blanks_and_uppercase_country():
    a = Address(name="Ada", street="Wilton Place", number="12",
                postal="00250", city="Helsinki", country="Finland")
    assert a.lines() == [
        ("name", "Ada"),
        ("body", "Wilton Place 12"),
        ("body", "00250 Helsinki"),
        ("body", "FINLAND"),
    ]


def test_csv_semicolon_and_bom(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text(
        "\ufeffname;street;number;postal;city;country\n"
        "Ada;Wilton Place;12;00250;Helsinki;Finland\n",
        encoding="utf-8",
    )
    rows = addresses.load(p)
    assert len(rows) == 1 and rows[0].name == "Ada"


def test_json_and_copies(tmp_path):
    p = tmp_path / "a.json"
    p.write_text('[{"name":"Ada","city":"Helsinki","copies":3}]', encoding="utf-8")
    rows = addresses.load(p)
    assert rows[0].copies == 3
    assert len(addresses.expand_copies(rows)) == 3


def test_blank_rows_are_skipped(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("name,city\nAda,Helsinki\n,\n,,\n", encoding="utf-8")
    assert len(addresses.load(p)) == 1


# =========================================================================
# Layout
# =========================================================================
def test_render_fills_the_print_area_exactly():
    spec = media.get("29x90")
    img, info = layout.render(Address(name="Ada", city="Helsinki"), spec)
    assert img.size == (spec.print_l, spec.print_w) == (991, 306)
    assert not info["overflow"]


def test_long_text_shrinks_but_stays_inside():
    spec = media.get("29x90")
    a = Address(
        name="Bartholomew Fitzgerald-Wintersmith III",
        att="Department of Exceptionally Long Names",
        street="Kauppapuistikkokatu", number="188", apartment="C 44",
        postal="00250", city="Helsinki", country="Finland",
    )
    img, info = layout.render(a, spec)
    assert img.size == (991, 306)
    assert info["body_size"] >= spec.print_w // 40


def test_rotation_produces_printer_space_width():
    spec = media.get("29x90")
    img, _ = layout.render(Address(name="Ada"), spec)
    for rot in ("cw", "ccw"):
        assert layout.to_printer_space(img, rot).size == (spec.print_w, spec.print_l)


def test_render_actually_marks_pixels():
    spec = media.get("29x90")
    img, _ = layout.render(Address(name="Ada", city="Helsinki"), spec)
    black = sum(1 for p in img.convert("L").tobytes() if p == 0)
    assert black > 200


# =========================================================================
# Protocol assembly
# =========================================================================
def _lines_for(spec, n=5):
    return [b"\x00" * media.RASTER_BYTES for _ in range(n)]


def _live_window(spec, opts):
    """Bit range within the 720-pin line that actually sits over the label."""
    start = spec.right_pins if opts.first_byte_is == "right" else spec.left_pins
    return start, start + spec.print_w


@pytest.mark.parametrize("mirror", [True, False])
def test_content_always_lands_inside_the_live_print_window(mirror):
    """Regression: mirroring must not push content off the media.

    Flipping the assembled 720-pin canvas (rather than the image) moved a
    29x90 label's content from pins 6..311 to pins 408..713 and printed two
    blank labels on real hardware.  Any set pixel must stay in the window.
    """
    from PIL import Image

    for key in ("29x90", "62x29", "17x54", "38x90"):
        spec = media.get(key)
        opts = JobOptions(mirror=mirror)
        img = Image.new("1", (spec.print_w, 2), 0)  # all black
        lines = protocol.image_to_raster_lines(img, spec, opts)
        lo, hi = _live_window(spec, opts)
        for line in lines:
            bits = "".join(f"{b:08b}" for b in line)
            assert bits[:lo] == "0" * lo, f"{key}: ink before the window"
            assert bits[hi:] == "0" * (media.PINS - hi), f"{key}: ink after the window"
            assert bits[lo:hi] == "1" * spec.print_w, f"{key}: window not filled"


def test_raster_line_places_data_at_the_right_offset():
    """Byte 0 is the right margin (section 3.2.5), confirmed on hardware."""
    from PIL import Image

    spec = media.get("29x90")
    img = Image.new("1", (spec.print_w, 1), 0)  # all black
    lines = protocol.image_to_raster_lines(img, spec, JobOptions())
    bits = "".join(f"{b:08b}" for b in lines[0])
    assert len(bits) == media.PINS
    assert bits[: spec.right_pins] == "0" * spec.right_pins
    assert bits[spec.right_pins : spec.right_pins + spec.print_w] == "1" * spec.print_w
    assert bits[spec.right_pins + spec.print_w :] == "0" * spec.left_pins


def test_mirror_reverses_the_axis_without_moving_the_window():
    """A mark on one edge swaps ends, but stays inside the print window."""
    from PIL import Image, ImageDraw

    spec = media.get("29x90")
    img = Image.new("1", (spec.print_w, 1), 1)
    ImageDraw.Draw(img).rectangle([0, 0, 7, 0], fill=0)  # leftmost 8 dots

    def first_set_bit(opts):
        line = protocol.image_to_raster_lines(img, spec, opts)[0]
        return "".join(f"{b:08b}" for b in line).index("1")

    plain = first_set_bit(JobOptions(mirror=False))
    flipped = first_set_bit(JobOptions(mirror=True))
    assert plain == spec.right_pins
    # Mirrored, that edge moves to the far end of the SAME window.
    assert flipped == spec.right_pins + spec.print_w - 8


def test_mirror_equals_pre_flipping_the_image():
    from PIL import Image, ImageDraw

    spec = media.get("29x90")
    img = Image.new("1", (spec.print_w, 3), 1)
    ImageDraw.Draw(img).rectangle([10, 0, 40, 2], fill=0)

    mirrored = protocol.image_to_raster_lines(img, spec, JobOptions(mirror=True))
    equivalent = protocol.image_to_raster_lines(
        img.transpose(Image.Transpose.FLIP_LEFT_RIGHT), spec, JobOptions(mirror=False)
    )
    assert mirrored == equivalent


def test_mirror_round_trips_through_the_decoder():
    """Encode -> strip margins -> un-reverse gives the original row back."""
    from PIL import Image, ImageDraw

    spec = media.get("29x90")
    img = Image.new("1", (spec.print_w, 4), 1)
    ImageDraw.Draw(img).rectangle([0, 0, 15, 3], fill=0)  # asymmetric marker

    lines = protocol.image_to_raster_lines(img, spec, JobOptions(mirror=True))
    for y, line in enumerate(lines):
        bits = "".join(f"{b:08b}" for b in line)
        window = bits[spec.right_pins : spec.right_pins + spec.print_w]
        recovered = window[::-1]
        original = "".join(
            "1" if img.getpixel((x, y)) == 0 else "0" for x in range(spec.print_w)
        )
        assert recovered == original


def test_job_header_and_terminator():
    spec = media.get("29x90")
    job = protocol.build_job([_lines_for(spec)], spec, JobOptions())
    assert job.startswith(b"\x00" * 200 + b"\x1b\x40" + b"\x1b\x69\x61\x01")
    assert job.endswith(b"\x1a")  # print with feed on the final page


def test_print_information_command_is_correct_for_29x90():
    spec = media.get("29x90")
    job = protocol.build_job([_lines_for(spec, 991)], spec, JobOptions())
    i = job.index(b"\x1b\x69\x7a")
    pi = job[i : i + 13]
    assert pi[3] & protocol.PI_KIND
    assert pi[3] & protocol.PI_WIDTH
    assert pi[3] & protocol.PI_LENGTH
    assert pi[3] & protocol.PI_RECOVER
    assert pi[4] == media.MEDIA_TYPE_DIE_CUT
    assert pi[5] == 29
    assert pi[6] == 90
    raster_n = pi[7] | (pi[8] << 8) | (pi[9] << 16) | (pi[10] << 24)
    assert raster_n == 991
    assert pi[11] == 0  # first page
    assert pi[12] == 0


def test_die_cut_forces_zero_feed():
    spec = media.get("29x90")
    job = protocol.build_job([_lines_for(spec)], spec, JobOptions(feed_dots=200))
    i = job.index(b"\x1b\x69\x64")
    assert job[i + 3] == 0 and job[i + 4] == 0


def test_continuous_media_clamps_feed():
    spec = media.get("62")
    job = protocol.build_job([_lines_for(spec)], spec, JobOptions(feed_dots=5))
    i = job.index(b"\x1b\x69\x64")
    feed = job[i + 3] | (job[i + 4] << 8)
    assert feed == media.CONTINUOUS_MIN_FEED_DOTS


def test_tiff_compression_is_selected():
    spec = media.get("29x90")
    job = protocol.build_job([_lines_for(spec)], spec, JobOptions())
    assert b"\x4d\x02" in job


def test_blank_lines_become_zero_raster():
    spec = media.get("29x90")
    job = protocol.build_job([_lines_for(spec, 10)], spec, JobOptions())
    body = job[job.index(b"\x4d\x02") + 2 : -1]
    assert body == b"\x5a" * 10


def test_multipage_uses_ff_then_sub():
    spec = media.get("29x90")
    job = protocol.build_job([_lines_for(spec), _lines_for(spec)], spec, JobOptions())
    assert job.count(b"\x1b\x69\x7a") == 2
    assert job.endswith(b"\x1a")
    assert b"\x0c" in job
    # Second page must be flagged as a continuation.
    second = job.rindex(b"\x1b\x69\x7a")
    assert job[second + 11] == 1


def test_auto_cut_commands_present():
    spec = media.get("29x90")
    job = protocol.build_job(
        [_lines_for(spec)], spec, JobOptions(auto_cut=True, cut_every=3)
    )
    i = job.index(b"\x1b\x69\x4d")
    assert job[i + 3] == protocol.MODE_AUTO_CUT
    j = job.index(b"\x1b\x69\x41")
    assert job[j + 3] == 3


def test_default_is_cut_once_at_the_end():
    """A run of labels should arrive as one strip, cut only at the end."""
    spec = media.get("29x90")
    opts = JobOptions()
    assert opts.auto_cut is False and opts.cut_at_end is True
    job = protocol.build_job([_lines_for(spec), _lines_for(spec)], spec, opts)
    i = job.index(b"\x1b\x69\x4d")
    k = job.index(b"\x1b\x69\x4b")
    assert job[i + 3] & protocol.MODE_AUTO_CUT == 0
    assert job[k + 3] & protocol.EXPANDED_CUT_AT_END
    assert b"\x1b\x69\x41" not in job  # meaningless without auto cut


def test_no_cut_clears_both_cutter_bits():
    """Disabling the cutter must clear ESC i M bit 6 AND ESC i K bit 3.

    Leaving 'cut at end' set still chops the roll once at the end of the job,
    which defeats the point of --no-cut.
    """
    spec = media.get("29x90")
    opts = JobOptions(auto_cut=False, cut_at_end=False)
    job = protocol.build_job([_lines_for(spec), _lines_for(spec)], spec, opts)

    i = job.index(b"\x1b\x69\x4d")
    assert job[i + 3] & protocol.MODE_AUTO_CUT == 0, "auto-cut bit still set"

    k = job.index(b"\x1b\x69\x4b")
    assert job[k + 3] & protocol.EXPANDED_CUT_AT_END == 0, "cut-at-end bit still set"

    assert b"\x1b\x69\x41" not in job


def test_cut_between_but_not_at_end():
    spec = media.get("29x90")
    opts = JobOptions(auto_cut=True, cut_at_end=False)
    job = protocol.build_job([_lines_for(spec)], spec, opts)
    i = job.index(b"\x1b\x69\x4d")
    k = job.index(b"\x1b\x69\x4b")
    assert job[i + 3] & protocol.MODE_AUTO_CUT
    assert job[k + 3] & protocol.EXPANDED_CUT_AT_END == 0


def test_cut_every_n_is_clamped_into_range():
    spec = media.get("29x90")
    for given, expected in ((0, 1), (1, 1), (10, 10), (255, 255), (999, 255)):
        job = protocol.build_job(
            [_lines_for(spec)], spec, JobOptions(auto_cut=True, cut_every=given)
        )
        j = job.index(b"\x1b\x69\x41")
        assert job[j + 3] == expected, f"cut_every={given}"


# --- CLI flag resolution -------------------------------------------------
def _cut_flags(argv):
    """Run the real parser and return (auto_cut, cut_every, cut_at_end)."""
    from kyyhky import cli

    args = cli.build_parser().parse_args(argv)
    return cli._resolve_cut(args)


def test_cli_cut_modes():
    base = ["print", "--to", "X"]
    assert _cut_flags(base) == (False, 1, True)                      # default
    assert _cut_flags(base + ["--cut", "each"]) == (True, 1, True)
    assert _cut_flags(base + ["--cut", "never"]) == (False, 1, False)
    assert _cut_flags(base + ["--no-cut"]) == (False, 1, False)
    # --cut-every implies "every", which is the headline request: every 10th.
    assert _cut_flags(base + ["--cut-every", "10"]) == (True, 10, True)
    assert _cut_flags(base + ["--cut", "every", "--cut-every", "10"]) == (True, 10, True)
    # --no-cut-at-end overrides whatever mode chose.
    assert _cut_flags(base + ["--cut-every", "10", "--no-cut-at-end"]) == (True, 10, False)
    assert _cut_flags(base + ["--cut", "each", "--no-cut-at-end"]) == (True, 1, False)


def test_cli_cut_every_10_reaches_the_wire():
    """End to end: --cut-every 10 must set ESC i A to 10 and enable auto cut."""
    from kyyhky import cli

    spec = media.get("29x90")
    args = cli.build_parser().parse_args(
        ["print", "--to", "X", "--city", "Helsinki", "--cut-every", "10"]
    )
    opts = cli._job_opts(args)
    job = protocol.build_job([_lines_for(spec)], spec, opts)
    i = job.index(b"\x1b\x69\x4d")
    j = job.index(b"\x1b\x69\x41")
    assert job[i + 3] & protocol.MODE_AUTO_CUT
    assert job[j + 3] == 10
    assert "every 10 labels" in cli._describe_cut(opts)


def test_high_resolution_bit():
    spec = media.get("29x90")
    job = protocol.build_job([_lines_for(spec)], spec, JobOptions(high_resolution=True))
    i = job.index(b"\x1b\x69\x4b")
    assert job[i + 3] & protocol.EXPANDED_HIGH_RESOLUTION


def test_status_parser():
    raw = bytearray(32)
    raw[0], raw[1], raw[2], raw[3], raw[4] = 0x80, 0x20, 0x42, 0x34, 0x33
    raw[10], raw[11], raw[17] = 29, 0x0B, 90
    raw[8] = 0x01
    st = protocol.parse_status(bytes(raw))
    assert st["model"] == "QL-580N"
    assert st["media_width_mm"] == 29 and st["media_length_mm"] == 90
    assert st["media_type"] == "die-cut"
    assert "no media" in st["errors"]


# =========================================================================
# End-to-end
# =========================================================================
def test_full_pipeline_produces_a_plausible_job():
    spec = media.get("29x90")
    a = Address(name="Ada Lovelace", street="Wilton Place", number="12",
                postal="00250", city="Helsinki", country="Finland")
    img, _ = layout.render(a, spec)
    lines = protocol.image_to_raster_lines(
        layout.to_printer_space(img, "cw"), spec, JobOptions()
    )
    assert len(lines) == spec.print_l == 991
    job = protocol.build_job([lines], spec, JobOptions())
    assert 1000 < len(job) < 400_000
    assert job.startswith(b"\x00" * 200)
    assert job.endswith(b"\x1a")
    # Non-blank content must have produced real raster transfers.
    assert job.count(b"\x67\x00") > 50
