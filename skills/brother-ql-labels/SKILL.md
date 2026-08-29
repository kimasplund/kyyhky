---
name: brother-ql-labels
description: "Print labels on a networked Brother QL printer."
version: 1.1.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Printing, Labels, Brother, QL-580N, Addresses, Barcode, QR, CSV]
---

# Brother QL labels (Kyyhky)

## When to Use

Use whenever the user wants something **printed on a physical label** —
addresses for post, shipping, parcels, filing, storage, asset tags, product
labels, name badges, or anything with a **bar code or QR code** on it.
Triggers include: "print a label", "print these addresses", "label this
parcel", "make asset tags", "print barcode labels", "QR labels for these
links", or handing over a CSV/spreadsheet.

Also use it to diagnose a QL-580N: blank labels, mirrored text, jobs that
vanish silently, or cutter behaviour.

**Not** for PDF/paper documents, and not for designing artwork.

## Two modes

1. **Addresses** — `kyyhky print --csv addr.csv`. Auto-sized type, no layout
   work needed.
2. **Anything else** — a JSON/YAML **template** listing elements (text, bar
   code, QR, image, line, box) at millimetre positions, filled from a CSV.
   Start with `kyyhky templates` to see the built-ins.

Reach for a template as soon as the label is not purely an address.

## Setup

```bash
pip install "kyyhky[all] @ git+https://github.com/kimasplund/kyyhky"
export KYYHKY_HOST=192.168.1.50      # or pass --host on every command
```

The `[all]` extra pulls in `segno` (QR), `python-barcode` and `PyYAML`.
Plain `pip install git+...` is enough for addresses only.

Find the printer if the address is unknown: `kyyhky discover`.

There is no Brother SDK for Linux for this model. Kyyhky speaks the raster
protocol directly on port 9100, per Brother's
*QL-500/550/560/570/580N/650TD/700/1050/1060N Raster Command Reference*.

## Quick reference

```bash
kyyhky discover                    # scan the LAN for printers
kyyhky status                      # is the printer reachable?
kyyhky media                       # supported label sizes
kyyhky fonts                       # usable font families
kyyhky templates                   # built-in custom layouts
kyyhky symbologies                 # bar code types + QR options
kyyhky sample --out addr.csv       # example CSV to fill in

# ALWAYS preview before printing -- it costs nothing
kyyhky preview --csv addr.csv --out labels.png --scale 2

kyyhky print --csv addr.csv --yes
kyyhky print --csv addr.csv --cut-every 10 --yes
kyyhky print --csv addr.csv --no-cut --yes    # leave on the roll

# one-off label, no file needed
kyyhky print --to 'Ada Lovelace' \
  --street 'Wilton Place' --number 12 --apartment 5 \
  --postal 'SW1X 8RL' --city London --country 'United Kingdom' --yes
```

Omit `--yes` for an interactive confirmation. `--dry-run` builds the job and
reports its size without sending anything.

Running from a clone instead of an install: `python3 -m kyyhky.cli ...`.

## Custom layouts (bar codes, QR, anything non-address)

```bash
kyyhky templates                              # what is built in
kyyhky template-init asset --out my.json      # copy one out to edit
kyyhky template-preview my.json --data items.csv --out check.png
kyyhky template-print my.json --data items.csv --yes
```

Built-ins: `address`, `shipping`, `asset`, `product`, `qr-only`,
`name-badge`. Preview any of them with no data at all —
`kyyhky template-preview asset --out a.png` renders a sample row.

A template is a media size plus a list of elements. **Positions are
millimetres from the top-left**, as you would measure with a ruler:

```json
{
  "label": "29x90",
  "elements": [
    {"type": "text",    "text": "{name}", "x": 4, "y": 2, "size": 4.5,
     "bold": true, "max_width": 52},
    {"type": "text",    "text": "{id}",   "x": 4, "y": 8, "size": 3.2},
    {"type": "barcode", "data": "{id}",   "x": 4, "y": 12.5,
     "width": 52, "height": 8, "symbology": "code128"},
    {"type": "qr",      "data": "{url}",  "x": -4, "y": 2, "size": 24}
  ]
}
```

| Type | Key options |
|------|-------------|
| `text` | `text`, `size` (mm), `bold`, `max_width`, `align`, `font` |
| `barcode` | `data`, `symbology`, `width` or `module`, `height`, `text_below` |
| `qr` | `data`, `size` or `module`, `ecc` (l/m/q/h), `micro` |
| `image` | `path`, `width`, `height`, `threshold`, `invert` |
| `line` | `x`, `y`, `x2`/`y2` or `length` + `vertical`, `thickness` |
| `box` | `x`, `y`, `width`, `height`, `thickness`, `filled` |

Rules worth knowing:

* `{column}` placeholders match **case-, space- and underscore-insensitively**,
  so `{Product Name}` and `{product_name}` read the same CSV column. A missing
  column renders empty rather than failing.
* **Negative `x`/`y` anchor to the right/bottom edge** — `"x": -4` sits 4 mm
  in from the right.
* Omit `x` and set `align` to `center`/`right`; omit `y` and set `valign` to
  `middle`/`bottom`.
* Any length can be given in dots with an `_dots` suffix (`"x_dots": 120`).
* `max_width` shrinks text to fit instead of running off the label.
* A `copies` column prints that row more than once.
* Without `--data`, use `--set key=value` (repeatable) for a one-off label.

Rendering **warns** when elements run off the label or **collide with each
other**. Read those warnings — they are the difference between a good label
and a wasted one.

## Bar codes and QR

`code128` is the right default for anything alphanumeric. `kyyhky symbologies`
lists all 13 with what each accepts.

Both are rendered from the raw module matrix at an **exact integer number of
printer dots per module**, never by scaling a bitmap. This is what makes small
codes scan reliably; quiet zones are included automatically. Verified on
hardware — printed labels scan with a phone.

Gotchas:

* `code39` appends a `$` checksum in most libraries. Kyyhky turns that **off**
  by default; pass `"checksum": true` if you need it.
* `ean13`/`upca` require an exact digit count and add their own check digit.
  A wrong length is an error naming the offending element.
* QR `size` is the box it must fit inside; the module size is computed from it.
  A tight `size` on a busy payload gives a code too dense to scan — prefer
  fewer characters (a short URL) over a smaller module.

## Label layout

Bold recipient name, then one line each, blanks skipped automatically:

```
Ada Lovelace             <- bold, largest
att: Purchasing          <- only if an att/c-o column exists
Wilton Place 12 as 5     <- street + number + apartment
SW1X 8RL London          <- postal + city
UNITED KINGDOM           <- country, upper-cased
```

Font size is chosen automatically: the renderer starts at `--max-size` and
steps down until everything fits inside the 306 x 991 dot print area. A typical
address lands at 66/50 pt; long ones shrink gracefully. If a label bottoms out
at `--min-size` the CLI flags it as `<-- TIGHT` in the listing.

## CSV format

Headers are matched case-insensitively in English, Finnish and Swedish, so most
spreadsheet exports work unchanged. Delimiter (`,` `;` tab `|`) is sniffed, and
a UTF-8 BOM is handled -- European Excel exports just work.

```csv
name,att,street,number,apartment,postal,city,country,copies
Ada Lovelace,,Wilton Place,12,5,SW1X 8RL,London,United Kingdom,1
Example Oy,Purchasing,Mannerheimintie,140,A 3,00250,Helsinki,Finland,2
```

| Field | Also accepted |
|-------|---------------|
| `name` | recipient, to, company, nimi, vastaanottaja, namn |
| `att` | attn, attention, c/o, care_of, dept, osasto |
| `street` | address, address1, katuosoite, katu, gata, adress |
| `number` | no, nr, house_number, nro, numero |
| `apartment` | apt, flat, unit, suite, address2, as, asunto, lgh |
| `postal` | zip, postcode, post_no, postinumero, postnummer |
| `city` | town, postitoimipaikka, kaupunki, ort, postort |
| `country` | maa, land |
| `copies` | qty, quantity, kpl, antal |

`.json` and `.jsonl` work too. `copies` prints that record N times.

Bare numeric apartments gain a qualifier (`5` -> `as 5`); anything already
qualified (`A 3`, `apt 9`) is left alone. An `att` value that already starts
with `att`/`attn`/`c/o` is not double-prefixed.

## Hardware facts (verified on real hardware)

These four cost real labels to establish. Do not re-derive them.

1. **TIFF/PackBits compression is mandatory over LAN.** Section 5: *"In case
   QL-580N/1060N, serial and LAN interface should set TIFF option."* An
   uncompressed job over 9100 will not print. Never pass `--no-compress`.

2. **Byte 0 of a raster line is the RIGHT margin.** Section 3.2.5. Offsetting
   by the left margin instead prints a **blank label** -- content lands on pins
   that overhang the media. (The popular `brother_ql` library assumes the
   opposite; for this model that is wrong.)

3. **The pin axis runs right-to-left, so the image must be pre-flipped.**
   Without it the text prints **mirrored**. Critically, flip the *image* before
   pasting it at the margin offset -- flipping the assembled 720-pin canvas
   moves content from pins 6..311 to 408..713 and prints blank.

4. **No status over Ethernet.** `ESC i S` never answers on the LAN board;
   section 6.9 defines no status channel for network printing. `status`
   reporting "not reported" is normal, not a fault. Printing still works.
   Media/error detection is USB/serial only.

## Cutting

**Default: one cut at the end of the job.** A run of address labels comes off
as a single strip, which is easier to carry and stick than loose labels.

| Flag | Effect |
|------|--------|
| *(default)* | `--cut end` — one cut after the whole run |
| `--cut each` | cut after every label |
| `--cut-every 10` | cut after every 10 labels (implies `--cut every`) |
| `--no-cut` | cutter fully off, labels stay on the roll |
| `--no-cut-at-end` | suppress the final cut, whatever `--cut` says |

Two independent bits drive the cutter and both matter:

| Mode | `ESC i M` | `ESC i K` | `ESC i A` |
|------|-----------|-----------|-----------|
| `end` (default) | `0x00` | `0x08` | — |
| `each` | `0x40` | `0x08` | 1 |
| `--cut-every 10` | `0x40` | `0x08` | 10 |
| `never` | `0x00` | `0x00` | — |

`ESC i M` bit 6 cuts *between* labels; `ESC i K` bit 3 cuts after the *final*
label. Clearing only the first still chops the roll once at the end — that is
why `--no-cut` must clear both. `ESC i A` is only emitted when auto-cut is on.

`python3 tools/show_cut_modes.py` prints this table from live job bytes.

The `print` command reports the mode in its header (`Cut : ...`) before
sending, so the behaviour is never a surprise.

## Geometry (29 x 90 mm)

| Property | Value |
|----------|-------|
| Print area | 306 x 991 dots (25.91 x 83.90 mm) |
| Left / right margin pins | 408 / 6 |
| Raster line | 90 bytes (720 pins) |
| Resolution | 300 dpi (`--hires` gives 600 dpi lengthwise) |

`kyyhky media` lists all 18 supported sizes. Continuous tape also needs
`--length MM`.

## Useful options

| Flag | Purpose |
|------|---------|
| `--limit N` | only the first N **rows** (a row with `copies=2` still yields 2 labels) |
| `--font` | `dejavu` (default, full Nordic coverage), `inter`, `liberation`, `condensed`, or a path |
| `--align center` / `--valign top\|middle\|bottom` | text placement |
| `--pad MM` | inner margin, default 1.6 |
| `--border` | hairline around the printable area (calibration aid) |
| `--rotate cw\|ccw` | reading direction; cosmetic on 29x90, both read fine |
| `--save-job FILE` | dump the raw job bytes for inspection |

## Verifying without wasting labels

`tools/decode_job.py` parses a built job back into a PNG, reports which pins
the ink occupies, and warns if anything falls outside the printable window:

```bash
python3 tools/decode_job.py            # writes /tmp/decoded_as_printed.png
```

`as_printed` models the pin-axis reversal, so it shows what the label will
physically look like. Use this before any risky change to the raster path.

`kyyhky calibrate` prints two probe labels (`cw` and `ccw`) with borders, for
when media or firmware changes.

## Troubleshooting

| Symptom | Cause |
|---------|-------|
| `no printer address` | pass `--host <ip>` or export `KYYHKY_HOST` |
| Blank labels | content outside the print window -- check the margin offset and that mirroring is applied to the image, not the canvas |
| Mirrored text | pin axis not reversed (`--no-mirror` was passed) |
| Nothing prints, no error | compression disabled -- LAN needs TIFF |
| `status` shows nothing | expected; the Ethernet board has no status channel |
| `discover` finds nothing | cold ARP cache; it already sweeps twice, try `--timeout 1.0` |
| Text clipped | shorten the address or lower `--min-size`; watch for `<-- TIGHT` |
| Bar code will not scan | narrow bar too thin — raise `module` to 2-3 dots, or give the element more `width` |
| QR will not scan | payload too long for the box; shorten the URL or raise `size` |
| `unknown symbology 'qr'` | QR is its own element type, not a symbology |
| `code39` prints a trailing `$` | that is the checksum; it is off by default here |
| Elements printed on top of each other | the render warning said so — read it and adjust |
| Template ignored the media | `--media` overrides the template; drop it to use the template's own |

## Tests

```bash
python3 -m pytest tests/ -q
```

86 tests, green on Python 3.9-3.13 in CI. The PackBits encoder is validated
against Brother's own worked example from the manual, every media spec is
checked to account for all 720 pins, and a regression test asserts ink never
lands outside the live print window (the bug that produced blank labels).

Code tests decode a rendered row back into run lengths and assert **every bar
and QR module is an exact whole number of dots wide** — the property that
decides whether a small printed code scans. Every built-in template is
rendered and checked for overflow and collisions.
