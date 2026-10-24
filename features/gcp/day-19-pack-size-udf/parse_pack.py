# Day 19 Google Cloud: the Python UDF body. cloud.sh pastes this file verbatim into CREATE FUNCTION ... LANGUAGE python
# (entry_point "parse_pack"); the local run imports it. Standard library only, so BigQuery builds the container
# without downloading packages.
import re

_FACTORS = {  # unit -> (base unit, factor)
    "mg": ("g", 0.001), "g": ("g", 1.0), "kg": ("g", 1000.0),
    "oz": ("g", 28.349523125), "lb": ("g", 453.59237), "lbs": ("g", 453.59237),
    "ml": ("ml", 1.0), "l": ("ml", 1000.0), "floz": ("ml", 29.5735295625),
    "qt": ("ml", 946.352946), "gal": ("ml", 3785.411784),
}
_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*(fl\.?\s*oz|kg|mg|g|oz|lbs|lb|ml|l|qt|gal)(?![a-z])")
_COUNT = re.compile(r"(\d+)\s*(?:x|pk|pack|ct|count)(?![a-z])")


def parse_pack(label):
    """'6 x 330ml' -> {'base_qty': 1980, 'unit': 'ml'}; '1.5 lb' -> 680 g; '12ct' -> 12 ct; unreadable -> NULLs."""
    if label is None:
        return {"base_qty": None, "unit": None}
    text = label.lower().replace("×", "x")
    amounts = list(_AMOUNT.finditer(text))
    if amounts:
        amount = amounts[-1]
        unit, factor = _FACTORS[re.sub(r"[\s.]", "", amount.group(2))]
        packs = _COUNT.search(text[:amount.start()])
        total = float(amount.group(1)) * factor * (int(packs.group(1)) if packs else 1)
        return {"base_qty": int(total + 0.5), "unit": unit}
    packs = _COUNT.search(text)
    if packs:
        return {"base_qty": int(packs.group(1)), "unit": "ct"}
    return {"base_qty": None, "unit": None}
