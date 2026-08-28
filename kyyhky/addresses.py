"""Address records: parsing from CSV/TSV/JSON and formatting into label lines.

The column names accepted are deliberately generous (English, Finnish and
Swedish) so a spreadsheet exported from almost anywhere just works.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

#: field -> accepted column headings (lower-cased, non-alphanumerics stripped)
ALIASES: dict[str, tuple[str, ...]] = {
    "name": (
        "name", "recipient", "to", "fullname", "contact", "company",
        "nimi", "vastaanottaja", "namn", "mottagare",
    ),
    "att": (
        "att", "attn", "attention", "careof", "co", "cof", "forattentionof",
        "fao", "att2", "line2", "department", "dept",
        "osasto", "attention_to",
    ),
    "street": (
        "street", "address", "address1", "addressline1", "addr", "addr1",
        "streetaddress", "road",
        "katuosoite", "katu", "osoite", "gata", "gatuadress", "adress",
    ),
    "number": (
        "number", "no", "num", "nr", "houseno", "housenumber", "streetno",
        "streetnumber", "building", "buildingno",
        "nro", "numero", "talonumero",
    ),
    "apartment": (
        "apartment", "apt", "flat", "unit", "suite", "door", "address2",
        "addressline2", "addr2",
        "as", "asunto", "huoneisto", "lagenhet", "lgh",
    ),
    "postal": (
        "postal", "postalcode", "postcode", "postno", "post", "zip",
        "zipcode", "plz", "cp",
        "postinumero", "postnummer",
    ),
    "city": (
        "city", "town", "locality", "place",
        "postitoimipaikka", "toimipaikka", "kaupunki", "ort", "stad", "postort",
    ),
    "country": (
        "country", "nation", "state",
        "maa", "land",
    ),
    "copies": ("copies", "qty", "quantity", "count", "kpl", "antal"),
}

_LOOKUP: dict[str, str] = {
    alias: field_name for field_name, aliases in ALIASES.items() for alias in aliases
}


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (key or "").strip().lower())


def _clean(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


@dataclass
class Address:
    """One recipient."""

    name: str = ""
    att: str = ""
    street: str = ""
    number: str = ""
    apartment: str = ""
    postal: str = ""
    city: str = ""
    country: str = ""
    copies: int = 1
    extra: dict = field(default_factory=dict)

    # -- construction ------------------------------------------------------
    @classmethod
    def from_mapping(cls, row: dict) -> "Address":
        data: dict[str, str] = {}
        extra: dict[str, str] = {}
        for raw_key, raw_val in row.items():
            field_name = _LOOKUP.get(_norm_key(raw_key))
            val = _clean(raw_val)
            if field_name is None:
                if val:
                    extra[str(raw_key)] = val
                continue
            # First non-empty wins, so "address" doesn't clobber "address1".
            if val and not data.get(field_name):
                data[field_name] = val

        copies = 1
        if data.get("copies"):
            try:
                copies = max(1, int(float(data["copies"])))
            except ValueError:
                copies = 1
        data.pop("copies", None)
        return cls(**data, copies=copies, extra=extra)

    # -- rendering ---------------------------------------------------------
    @property
    def street_line(self) -> str:
        """'Wilton Place 12 A 5' from street / number / apartment."""
        parts = [self.street, self.number]
        if self.apartment:
            apt = self.apartment
            # A bare token like "5" reads better as "as 5"; "A 5" or "apt 4"
            # already carries its own qualifier, so leave those alone.
            if re.fullmatch(r"\d+[a-zA-Z]?", apt):
                apt = f"as {apt}"
            parts.append(apt)
        return " ".join(p for p in parts if p)

    @property
    def city_line(self) -> str:
        return " ".join(p for p in (self.postal, self.city) if p)

    @property
    def att_line(self) -> str:
        if not self.att:
            return ""
        if re.match(r"^\s*(att|attn|c/o|c\.o\.|för|for)\b", self.att, re.I):
            return self.att
        return f"att: {self.att}"

    def lines(self, upper_country: bool = True) -> list[tuple[str, str]]:
        """Label body as ``(style, text)`` pairs, blanks dropped.

        Styles are ``"name"`` (bold, largest) and ``"body"``.
        """
        out: list[tuple[str, str]] = []
        if self.name:
            out.append(("name", self.name))
        if self.att_line:
            out.append(("body", self.att_line))
        if self.street_line:
            out.append(("body", self.street_line))
        if self.city_line:
            out.append(("body", self.city_line))
        if self.country:
            country = self.country.upper() if upper_country else self.country
            out.append(("body", country))
        return out

    def is_empty(self) -> bool:
        return not any(
            (self.name, self.att, self.street, self.number,
             self.apartment, self.postal, self.city, self.country)
        )

    def one_line(self) -> str:
        bits = [t for _, t in self.lines()]
        return " / ".join(bits) if bits else "(empty)"


# =========================================================================
# Loading
# =========================================================================
def _sniff_dialect(sample: str) -> csv.Dialect | type[csv.Dialect]:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def load(path: str | Path) -> list[Address]:
    """Load addresses from .csv / .tsv / .json / .jsonl.

    CSV delimiter (``,`` ``;`` tab ``|``) is sniffed, so Excel exports from
    Finnish locales (which use ``;``) work without a flag.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")
    suffix = p.suffix.lower()

    rows: list[dict]
    if suffix == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("addresses", "recipients", "rows", "data", "items"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                data = [data]
        rows = [r for r in data if isinstance(r, dict)]
    elif suffix == ".jsonl" or suffix == ".ndjson":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        sample = text[:8192]
        reader = csv.DictReader(text.splitlines(), dialect=_sniff_dialect(sample))
        rows = [r for r in reader]

    out: list[Address] = []
    for row in rows:
        addr = Address.from_mapping(row)
        if not addr.is_empty():
            out.append(addr)
    return out


def expand_copies(addresses: list[Address]) -> list[Address]:
    """Flatten the ``copies`` column into repeated records."""
    out: list[Address] = []
    for a in addresses:
        out.extend([a] * max(1, a.copies))
    return out
