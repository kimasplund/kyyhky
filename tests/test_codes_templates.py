"""Tests for bar codes, QR codes and custom layouts."""

import json

import pytest
from PIL import Image

from kyyhky import codes, media, template


# --- codes ---------------------------------------------------------------


def test_qr_matrix_is_square_and_binary():
    m = codes.qr_matrix("HELLO")
    assert len(m) == len(m[0])
    assert set(v for row in m for v in row) <= {0, 1}


def test_qr_image_size_is_exact():
    """Every module must be a whole number of dots across."""
    m = codes.qr_matrix("HELLO", ecc="m")
    img = codes.qr_image("HELLO", module_dots=4, ecc="m", quiet=4)
    expected = (len(m) + 8) * 4
    assert img.size == (expected, expected)


def test_qr_modules_land_on_exact_dot_boundaries():
    """A scaled bitmap gives uneven bars; matrix rendering must not."""
    img = codes.qr_image("SCAN-ME-123", module_dots=5, quiet=4)
    px = img.load()
    row = [px[x, 5 * 4 + 2] for x in range(img.width)]
    runs, current, n = [], row[0], 1
    for value in row[1:]:
        if value == current:
            n += 1
        else:
            runs.append(n)
            current, n = value, 1
    runs.append(n)
    assert all(r % 5 == 0 for r in runs), f"uneven runs: {runs}"


def test_qr_includes_a_quiet_zone():
    """Without a quiet zone many scanners simply refuse to read."""
    img = codes.qr_image("X", module_dots=3, quiet=4)
    px = img.load()
    for x in range(img.width):
        assert px[x, 0] == 1
    for y in range(img.height):
        assert px[0, y] == 1


def test_qr_ecc_levels_change_the_symbol():
    low = len(codes.qr_matrix("some payload here", ecc="l"))
    high = len(codes.qr_matrix("some payload here", ecc="h"))
    assert high >= low


def test_qr_rejects_a_bad_ecc_level():
    with pytest.raises(codes.CodeError, match="error correction"):
        codes.qr_matrix("x", ecc="z")


def test_qr_fit_module_never_overflows():
    for target in (60, 100, 137, 250, 301):
        module = codes.qr_fit_module(target, "https://example.com/abc")
        img = codes.qr_image("https://example.com/abc", module_dots=module)
        assert img.width <= target


def test_barcode_modules_are_binary():
    pattern = codes.barcode_modules("ABC-123", "code128")
    assert set(pattern) <= {"0", "1"}
    assert len(pattern) > 20


def test_barcode_bars_are_exact_multiples_of_the_module():
    img = codes.barcode_image("ABC-123", "code128", module_dots=3,
                              height_dots=40, text=False, quiet=10)
    px = img.load()
    row = [px[x, 10] for x in range(img.width)]
    runs, current, n = [], row[0], 1
    for value in row[1:]:
        if value == current:
            n += 1
        else:
            runs.append(n)
            current, n = value, 1
    runs.append(n)
    assert all(r % 3 == 0 for r in runs), f"uneven bars: {runs}"


def test_barcode_width_follows_the_module_count():
    pattern = codes.barcode_modules("HELLO", "code128")
    img = codes.barcode_image("HELLO", "code128", module_dots=2,
                              height_dots=30, quiet=10, text=False)
    assert img.width == (len(pattern) + 20) * 2


def test_code39_checksum_is_off_by_default():
    """python-barcode appends '$' unless told otherwise -- surprising."""
    assert codes.barcode_text("ABC123", "code39") == "ABC123"
    assert codes.barcode_text("ABC123", "code39", checksum=True) != "ABC123"


def test_ean13_reports_its_check_digit():
    text = codes.barcode_text("400638133393", "ean13")
    assert len(text) == 13
    assert text.startswith("400638133393")


def test_symbology_aliases_resolve():
    for alias in ("code-128", "c128", "128", "CODE128"):
        assert codes.resolve_symbology(alias) == "code128"
    for alias in ("upc", "upc-a"):
        assert codes.resolve_symbology(alias) == "upca"


def test_unknown_symbology_lists_the_known_ones():
    with pytest.raises(codes.CodeError, match="code128"):
        codes.resolve_symbology("qr")


def test_bad_barcode_data_names_the_symbology():
    with pytest.raises(codes.CodeError, match="ean13"):
        codes.barcode_modules("12", "ean13")


# --- templates -----------------------------------------------------------


def test_placeholders_are_case_and_separator_insensitive():
    row = {"Product Name": "Widget"}
    for form in ("{product_name}", "{ProductName}", "{PRODUCT NAME}"):
        assert template.substitute(form, row) == "Widget"


def test_missing_placeholder_becomes_empty_not_an_error():
    assert template.substitute("[{nope}]", {"a": "b"}) == "[]"


def test_every_builtin_renders_without_warnings():
    for name in template.BUILTIN:
        tmpl = template.builtin(name)
        _img, info = template.render(tmpl, template.sample_row(tmpl))
        assert not info["warnings"], f"{name}: {info['warnings']}"


def test_every_builtin_matches_its_media_print_area():
    for name in template.BUILTIN:
        tmpl = template.builtin(name)
        spec = media.get(tmpl.label)
        img, _info = template.render(tmpl, template.sample_row(tmpl))
        assert img.size == (spec.print_l, spec.print_w), name


def test_portrait_media_is_authored_landscape_then_rotated():
    """62x29 is 62mm WIDE; the printer canvas is the tall way round."""
    tmpl = template.builtin("asset")
    spec = media.get("62x29")
    _img, info = template.render(tmpl, template.sample_row(tmpl))
    assert info["rotated"] is True
    assert info["canvas"] == (spec.print_w, spec.print_l)
    assert info["print_area"] == (spec.print_l, spec.print_w)


def test_overlapping_elements_are_reported():
    tmpl = template.parse({
        "label": "62x29",
        "elements": [
            {"type": "text", "text": "AAAAAAAAAAAA", "x": 3, "y": 3, "size": 6},
            {"type": "text", "text": "BBBBBBBBBBBB", "x": 5, "y": 4, "size": 6},
        ],
    })
    _img, info = template.render(tmpl, {})
    assert any("overlap" in w for w in info["warnings"])


def test_decorative_boxes_do_not_trigger_overlap_warnings():
    """A box drawn around text is deliberate, not a collision."""
    tmpl = template.parse({
        "label": "62x29",
        "elements": [
            {"type": "box", "x": 2, "y": 2, "width": 50, "height": 20},
            {"type": "text", "text": "INSIDE", "x": 6, "y": 8, "size": 4},
        ],
    })
    _img, info = template.render(tmpl, {})
    assert not info["warnings"]


def test_overlap_can_be_allowed_explicitly():
    tmpl = template.parse({
        "label": "62x29",
        "elements": [
            {"type": "text", "text": "AAAAAAAA", "x": 3, "y": 3, "size": 6},
            {"type": "text", "text": "BBBBBBBB", "x": 4, "y": 4, "size": 6,
             "overlap_ok": True},
        ],
    })
    _img, info = template.render(tmpl, {})
    assert not info["warnings"]


def test_negative_x_anchors_to_the_right_edge():
    tmpl = template.parse({
        "label": "62x29",
        "elements": [{"type": "text", "text": "R", "x": -3, "y": 3, "size": 4}],
    })
    spec = media.get("62x29")
    width = spec.print_l if spec.print_l >= spec.print_w else spec.print_w
    img, info = template.render(tmpl, {})
    canvas_w = info["canvas"][0]
    if info["rotated"]:
        img = img.transpose(Image.Transpose.ROTATE_90)
    inked = [x for x in range(img.width)
             if any(img.getpixel((x, y)) == 0 for y in range(img.height))]
    assert min(inked) > canvas_w * 0.5, "right-anchored text drifted left"
    assert width  # spec sanity


def test_text_shrinks_to_fit_max_width():
    tmpl = template.parse({
        "label": "62x100",
        "elements": [{"type": "text", "text": "{name}", "x": 3, "y": 3,
                      "size": 8, "max_width": 30}],
    })
    _img, info = template.render(tmpl, {"name": "A Very Long Name Indeed"})
    assert not info["warnings"]


def test_out_of_range_position_is_reported():
    tmpl = template.parse({
        "label": "62x29",
        "elements": [{"type": "text", "text": "X", "x": 200, "y": 3,
                      "size": 4}],
    })
    _img, info = template.render(tmpl, {})
    assert any("past the label" in w for w in info["warnings"])


def test_unknown_element_type_names_the_index():
    with pytest.raises(template.TemplateError, match="element 1"):
        template.parse({
            "label": "62x29",
            "elements": [
                {"type": "text", "text": "ok"},
                {"type": "hologram"},
            ],
        })


def test_bad_barcode_data_names_the_element():
    tmpl = template.parse({
        "label": "62x29",
        "elements": [{"type": "barcode", "data": "12", "symbology": "ean13",
                      "x": 3, "y": 3}],
    })
    with pytest.raises(template.TemplateError, match="element 0"):
        template.render(tmpl, {})


def test_empty_template_is_rejected():
    with pytest.raises(template.TemplateError, match="no elements"):
        template.parse({"label": "62x29", "elements": []})


def test_continuous_media_needs_a_length():
    tmpl = template.parse({
        "label": "62",
        "elements": [{"type": "text", "text": "x", "x": 1, "y": 1}],
    })
    with pytest.raises(template.TemplateError, match="length"):
        template.render(tmpl, {})


def test_continuous_media_accepts_length_from_the_template():
    tmpl = template.parse({
        "label": "62",
        "length_mm": 40,
        "elements": [{"type": "text", "text": "x", "x": 1, "y": 1}],
    })
    img, _info = template.render(tmpl, {})
    # Printer space: the requested length runs across the image, the media
    # width down it.
    assert img.size == (media.mm_to_dots(40), media.get("62").print_w)


def test_columns_are_discovered_from_placeholders():
    tmpl = template.builtin("shipping")
    assert "tracking" in tmpl.columns
    assert "name" in tmpl.columns


def test_csv_rows_round_trip():
    rows = template.read_rows_text("name,id\nAda,1\nGrace,2\n")
    assert rows == [{"name": "Ada", "id": "1"}, {"name": "Grace", "id": "2"}]


def test_semicolon_csv_is_detected():
    rows = template.read_rows_text("name;id\nAda;1\n")
    assert rows == [{"name": "Ada", "id": "1"}]


def test_jsonl_rows_round_trip():
    rows = template.read_rows_text('{"name": "Ada"}\n{"name": "Grace"}\n')
    assert rows == [{"name": "Ada"}, {"name": "Grace"}]


def test_builtin_json_is_valid_json():
    for name in template.BUILTIN:
        parsed = json.loads(template.builtin_json(name))
        assert parsed["elements"]


def test_template_round_trips_through_json():
    for name in template.BUILTIN:
        text = template.builtin_json(name)
        tmpl = template.loads(text)
        _img, info = template.render(tmpl, template.sample_row(tmpl))
        assert not info["warnings"], f"{name}: {info['warnings']}"


def test_qr_element_shrinks_to_the_space_left_below_it():
    """A wide-but-short gap must not overflow the bottom edge."""
    tmpl = template.parse({
        "label": "62x29",
        "elements": [{"type": "qr", "data": "https://example.com",
                      "x": 3, "y": 20, "size": 40}],
    })
    _img, info = template.render(tmpl, {})
    assert not info["warnings"]


def test_rendered_labels_actually_contain_ink():
    for name in template.BUILTIN:
        tmpl = template.builtin(name)
        img, _info = template.render(tmpl, template.sample_row(tmpl))
        assert 0 in set(img.convert("L").tobytes()), name
