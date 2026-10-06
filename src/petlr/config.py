"""Shared paths and constants.

Everything that more than one module needs lives here so there is a single
place to change it.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("PETLR_DATA_DIR", ROOT / "data")).resolve()
RAW_DIR = DATA_DIR / "raw"
CLEAN_DIR = DATA_DIR / "clean"
OUT_DIR = DATA_DIR / "out"

SEED = int(os.getenv("PETLR_SEED", "42"))

# Claims above this amount (per claim) are "large". The model projects the
# capped part from each group's own experience and adds large claims back as
# a portfolio-wide load, because one $20k surgery says little about a group.
LARGE_CLAIM_CAP = 5_000.0

REGIONS = ["Northeast", "Southeast", "Midwest", "Southwest", "West"]
DEDUCTIBLES = [100, 250, 500]
SPECIES_MIXES = ["dog_heavy", "mixed", "cat_heavy"]
SIZE_BANDS = ["small", "medium", "large", "jumbo"]
TENURE_BANDS = ["new", "established", "mature"]


def species_mix_label(dog_share: float) -> str:
    """Bucket a group's share of dogs into a readable label."""
    if dog_share >= 0.75:
        return "dog_heavy"
    if dog_share >= 0.40:
        return "mixed"
    return "cat_heavy"


def size_band(pets: float) -> str:
    """Bucket a group's pet count: small <50, medium <250, large <1000, jumbo."""
    if pets < 50:
        return "small"
    if pets < 250:
        return "medium"
    if pets < 1000:
        return "large"
    return "jumbo"


def tenure_band(months: float) -> str:
    """Bucket tenure: new <12 months, established <36 months, mature 36+."""
    if months < 12:
        return "new"
    if months < 36:
        return "established"
    return "mature"
