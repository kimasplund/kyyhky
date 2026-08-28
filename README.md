# Kyyhky

Address labels for the **Brother QL-580N** over plain TCP — no vendor SDK, no
CUPS driver, no printer-specific dependencies. Just Python, Pillow, and the
raster protocol from Brother's official command reference.

Brother ships no Linux SDK for this model, so Kyyhky speaks port 9100 directly.

```bash
kyyhky discover                                  # find the printer
export KYYHKY_HOST=192.168.1.50
kyyhky sample --out addr.csv                     # example CSV to edit
kyyhky preview --csv addr.csv --out labels.png   # check before you print
kyyhky print --csv addr.csv --yes
```

```
┌──────────────────────────────────────┐
│  Ada Lovelace                        │   ← bold, largest
│  att: Purchasing                     │   ← only when present
│  Wilton Place 12 as 5                │   ← street + number + apartment
│  SW1X 8RL London                     │   ← postal + city
│  UNITED KINGDOM                      │   ← country, upper-cased
└──────────────────────────────────────┘
```

Type size is picked automatically: the renderer starts large and steps down
until everything fits the printable area. Blank fields are skipped without
leaving gaps.

## Use it from an AI coding agent

[`skills/brother-ql-labels/SKILL.md`](skills/brother-ql-labels/SKILL.md) is a
ready-made agent skill: it teaches the agent the commands, the CSV column
aliases, the cutter bits, and the four hardware gotchas below — so it does not
rediscover them by wasting labels.

```bash
# Claude Code (per project)
mkdir -p .claude/skills && cp -r skills/brother-ql-labels .claude/skills/

# Hermes (global)
cp -r skills/brother-ql-labels ~/.hermes/skills/
```

Then just ask: *"print these addresses"* and hand over a CSV.

## Install

```bash
pip install git+https://github.com/kimasplund/kyyhky
```

Or from a clone:

```bash
git clone https://github.com/kimasplund/kyyhky
cd kyyhky
pip install -e .
```

Requires Python 3.9+ and Pillow. On a minimal system also install a font —
`fonts-dejavu-core` on Debian/Ubuntu. Check what was found with `kyyhky fonts`.

## Supported hardware

Developed and verified against a **QL-580N** over Ethernet.

The raster protocol is shared across the QL family, so the QL-1060N (the other
networked model) and USB models piped through `/dev/usb/lp0` are likely to work
— but they are **untested**. Reports welcome.

Every documented media size is in `kyyhky/media.py`; `kyyhky media` lists all 18.

## Configuration

| Setting | Flag | Environment |
|---------|------|-------------|
| Printer address | `--host` | `KYYHKY_HOST` |
| Port | `--port` | `KYYHKY_PORT` (default 9100) |

## Input formats

CSV, TSV, JSON or JSONL. The delimiter (`,` `;` tab `|`) is sniffed and a UTF-8
BOM is handled, so spreadsheet exports work unchanged — including the `;` that
Excel emits in European locales.

```csv
name,att,street,number,apartment,postal,city,country,copies
Ada Lovelace,,Wilton Place,12,,SW1X 8RL,London,United Kingdom,1
Example Oy,Purchasing,Mannerheimintie,140,A 3,00250,Helsinki,Finland,2
```

Column headings are matched case-insensitively in English, Finnish and Swedish:

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

A `copies` column prints that record N times. Bare numeric apartments gain a
qualifier (`5` → `as 5`); already-qualified values (`A 3`, `apt 9`) are left
alone, and an `att` that already starts with `att`/`c/o` is not double-prefixed.

Or skip files entirely:

```bash
kyyhky print --to 'Ada Lovelace' --street 'Wilton Place' --number 12 \
             --postal 'SW1X 8RL' --city London --country 'United Kingdom' --yes
```

## Cutting

**Default: one cut at the end of the job**, so a run comes off as a single
strip.

| Flag | Effect |
|------|--------|
| *(default)* | `--cut end` — one cut after the whole run |
| `--cut each` | cut after every label |
| `--cut-every 10` | cut after every 10 labels |
| `--no-cut` | cutter off, labels stay on the roll |
| `--no-cut-at-end` | suppress the final cut |

Two independent bits drive the cutter: `ESC i M` bit 6 cuts *between* labels,
`ESC i K` bit 3 cuts after the *final* one. Clearing only the first still chops
the roll once at the end — which is why `--no-cut` clears both.

```
$ python tools/show_cut_modes.py
mode                          ESC i M  ESC i K  ESC i A   behaviour
----------------------------------------------------------------------------
default                          0x00     0x08        -   once at the end
--cut each                       0x40     0x08        1   after every label
--cut-every 10                   0x40     0x08       10   after every 10 labels
--no-cut                         0x00     0x00        -   never
```

## Four things this printer does that will surprise you

All four cost real labels to establish.

1. **TIFF/PackBits compression is mandatory over Ethernet.** From the command
   reference: *"In case QL-580N/1060N, serial and LAN interface should set TIFF
   option."* An uncompressed job is silently discarded — no error, no output.

2. **Byte 0 of a raster line is the *right* margin** (§3.2.5). Offsetting by
   the left margin puts content on pins that overhang the media, and the label
   comes out blank. Note the widely used `brother_ql` library assumes the
   opposite; for this model that is wrong.

3. **The pin axis runs right-to-left**, so the image must be flipped before it
   is placed. Skip it and text prints mirrored. Flip the *image*, not the
   assembled 720-pin canvas — flipping the canvas relocates a 29×90 label from
   pins 6–311 to 408–713 and prints blank.

4. **There is no status channel over Ethernet.** `ESC i S` never answers on the
   LAN board; §6.9 defines network printing as fire-and-forget. `status`
   reporting "not reported" is correct behaviour, not a fault. Media and error
   detection are USB/serial only.

## Verifying without wasting labels

`tools/decode_job.py` parses a built job back into a PNG, reports which pins
the ink occupies, and warns when anything falls outside the printable window:

```
$ python tools/decode_job.py
decoded 991 raster lines x 90 bytes
ink occupies pins (38, 280), live print window is (6, 311)
  ink is inside the printable window
wrote /tmp/decoded_as_printed.png  (29x90mm label)
```

`as_printed` models the pin-axis reversal, so it shows what the label will
physically look like. Use it before any change to the raster path.

`kyyhky calibrate` prints two probe labels with borders when media or firmware
changes.

## Commands

| Command | Purpose |
|---------|---------|
| `discover` | scan the LAN for label printers |
| `status` | check the printer is reachable |
| `media` | list all 18 supported label sizes |
| `fonts` | list usable font families |
| `sample` | write an example CSV |
| `preview` | render to PNG without printing |
| `print` | render and print |
| `calibrate` | print `cw`/`ccw` probe labels |

Useful options: `--limit N` (first N rows), `--font`, `--align`, `--valign`,
`--pad MM`, `--border`, `--rotate`, `--hires` (600 dpi lengthwise),
`--dry-run`, `--save-job FILE`.

## Geometry (29 × 90 mm)

| Property | Value |
|----------|-------|
| Print area | 306 × 991 dots (25.91 × 83.90 mm) |
| Left / right margin pins | 408 / 6 |
| Raster line | 90 bytes (720 pins) |
| Resolution | 300 dpi |

Continuous tape also needs `--length MM`.

## Tests

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q      # 47 passed
```

The PackBits encoder is validated against Brother's own worked example from the
manual, every media spec must account for exactly 720 pins, and a regression
test asserts ink never lands outside the live print window — the bug that
produced blank labels.

## Layout

```
kyyhky/
  media.py       label geometry, from the manual's tables
  protocol.py    PackBits, job assembly, TCP transport
  addresses.py   parsing and formatting
  layout.py      typography, font discovery, auto-fit
  cli.py         command line
skills/
  brother-ql-labels/  agent skill (Claude Code / Hermes)
tools/
  decode_job.py     turn job bytes back into a picture
  show_cut_modes.py show the cutter bits each mode emits
tests/
```

## Reference

Brother, *QL-500/550/560/570/580N/650TD/700/1050/1060N Raster Command
Reference* — [PDF](https://download.brother.com/welcome/docp000678/cv_qlseries_eng_raster_600.pdf).

## Licence

MIT — see [LICENSE](LICENSE).
