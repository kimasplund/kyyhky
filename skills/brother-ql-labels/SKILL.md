---
name: brother-ql-labels
description: "Print address labels on a networked Brother QL-580N."
version: 1.0.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Printing, Labels, Brother, QL-580N, Addresses, CSV, Raster]
---

# Brother QL-580N address labels (Kyyhky)

## When to Use

Use whenever the user wants something **printed on a physical label** — address
labels for post, shipping, parcels, filing, or storage. Triggers include:
"print a label", "print these addresses", "label this parcel", "send this to
the label printer", or handing over a CSV/spreadsheet of recipients.

Also use it to diagnose a QL-580N: blank labels, mirrored text, jobs that
vanish silently, or cutter behaviour.

**Not** for PDF/paper documents, and not for designing artwork.

## Setup

```bash
pip install git+https://github.com/kimasplund/kyyhky
export KYYHKY_HOST=192.168.1.50      # or pass --host on every command
```

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

## Tests

```bash
python3 -m pytest tests/ -q
```

47 tests, green on Python 3.9-3.13 in CI. The PackBits encoder is validated
against Brother's own worked example from the manual, every media spec is
checked to account for all 720 pins, and a regression test asserts ink never
lands outside the live print window (the bug that produced blank labels).
