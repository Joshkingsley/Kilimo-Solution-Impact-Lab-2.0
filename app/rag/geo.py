"""County / depot resolution helpers (SPEC §9.2 step 4).

Deterministic lookups only. The depot map is deliberately small and hand-verified;
extend it from documents in the corpus, never from farmer text.
"""
from __future__ import annotations

import re

COUNTIES: tuple[str, ...] = (
    "Baringo", "Bomet", "Bungoma", "Busia", "Elgeyo-Marakwet", "Embu", "Garissa", "Homa Bay",
    "Isiolo", "Kajiado", "Kakamega", "Kericho", "Kiambu", "Kilifi", "Kirinyaga", "Kisii", "Kisumu",
    "Kitui", "Kwale", "Laikipia", "Lamu", "Machakos", "Makueni", "Mandera", "Marsabit", "Meru",
    "Migori", "Mombasa", "Murang'a", "Nairobi", "Nakuru", "Nandi", "Narok", "Nyamira", "Nyandarua",
    "Nyeri", "Samburu", "Siaya", "Taita-Taveta", "Tana River", "Tharaka-Nithi", "Trans Nzoia",
    "Turkana", "Uasin Gishu", "Vihiga", "Wajir", "West Pokot",
)

# depot (lower-case) -> county. Hand-maintained; entries must be backed by a corpus document.
DEPOTS: dict[str, str] = {
    "kangundo": "Machakos",
    "machakos": "Machakos",
    "athi river": "Machakos",
    "kakamega": "Kakamega",
    "mumias": "Kakamega",
    "eldoret": "Uasin Gishu",
    "moi's bridge": "Uasin Gishu",
    "kitale": "Trans Nzoia",
    "nakuru": "Nakuru",
    "thika": "Kiambu",
    "naivasha": "Nakuru",
    "nairobi": "Nairobi",
    # wards / towns named in the Day-0 corpus and eval cases
    "matungulu": "Machakos", "tala": "Machakos", "mitaboni": "Machakos", "masii": "Machakos", "kathiani": "Machakos",
    "mwala": "Machakos", "yatta": "Machakos", "mavoko": "Machakos",
    "malava": "Kakamega", "butere": "Kakamega", "shinyalu": "Kakamega", "lurambi": "Kakamega", "matungu": "Kakamega",
    "likuyani": "Kakamega", "navakholo": "Kakamega", "khwisero": "Kakamega", "ikolomani": "Kakamega", "lugari": "Kakamega",
}

_COUNTY_LOOKUP = {c.lower(): c for c in COUNTIES}
_COUNTY_LOOKUP.update({c.lower().replace("-", " "): c for c in COUNTIES})
_COUNTY_LOOKUP.update({c.lower().replace("'", ""): c for c in COUNTIES})


def normalise_county(value: str | None) -> str | None:
    if not value:
        return None
    key = re.sub(r"\s+", " ", value.strip().lower())
    key = re.sub(r"\bcounty\b", "", key).strip()
    return _COUNTY_LOOKUP.get(key)


def normalise_depot(value: str | None) -> str | None:
    if not value:
        return None
    key = re.sub(r"\s+", " ", value.strip().lower())
    key = re.sub(r"\b(depot|ncpb|store)\b", "", key).strip()
    return key.title() if key in DEPOTS else None


def county_for_depot(depot: str | None) -> str | None:
    if not depot:
        return None
    return DEPOTS.get(depot.strip().lower())


def find_in_text(text: str) -> tuple[str | None, str | None]:
    """Best-effort (county, depot) mention scan. Whole-word, case-insensitive."""
    low = " " + re.sub(r"[^\w' ]+", " ", text.lower()) + " "
    depot = next((d for d in sorted(DEPOTS, key=len, reverse=True) if f" {d} " in low), None)
    county = next((c for k, c in _COUNTY_LOOKUP.items() if f" {k} " in low), None)
    if depot and not county:
        county = DEPOTS[depot]
    return county, (depot.title() if depot else None)
