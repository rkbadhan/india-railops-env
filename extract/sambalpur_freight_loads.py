"""
Goods-train load tables for Sambalpur division -> rail_data/processed/freight_loads.csv

Usage:
    .venv/Scripts/python.exe extract/sambalpur_freight_loads.py

The 2023 WTT we extract timetables from stops at Chapter III, so these come
from the 2026 WTT (WTT_ECoR_Sambalpur_2026.pdf), Chapter VII, pages 177
(diesel) and 178 (electric). That PDF is scanned images with no text layer:
the values below were read off the rendered pages by eye and transcribed by
hand. Only the SBP division rows are included.
"""

import csv
import os
import re

SOURCE = "WTT_ECoR_Sambalpur_2026.pdf"
OUT = os.path.join("rail_data", "processed", "freight_loads.csv")

# Page 177: "Load Haulage Chart of Diesel Locomotives for different Sections".
# Every column is single-unit (SU), dry then wet weather.
DIESEL_LOCOS = ["WDM3D", "WDG3A", "WDG4", "WDG4D", "WDG4G", "WDG6G"]
DIESEL = [
    # section, ruling gradient, degree of curve, (dry, wet) per loco as printed
    ("JSG-SBP-JSG", "1/125", "2", "2895 2288 3045 2405 4010 3170 4138 3269 4136 3268 4400 3475"),
    ("SBP-TIG-SBP", "1/125", "3", "2804 2215 2950 2330 3885 3070 4010 3167 4007 3165 4260 3365"),
    ("SBP-TLHR-SBP", "1/150", "3", "3145 2495 3305 2775 4335 3445 4475 3550 4475 3550 4755 3780"),
    ("TIG-RSD-TIG", "1/150", "2", "3260 2585 3425 2880 4490 3565 4635 3680 4635 3680 4930 3915"),
    ("TIG-SPRD", "1/125", "4", "2718 2148 2860 2260 3768 2977 3885 3070 3885 3070 4130 3260"),
    ("SPRD-TIG", "1/100", "4", "2365 1870 2485 2085 3270 2590 3370 2670 3370 2670 3585 2840"),
]

# Page 178: "Load Chart for Goods Trains hauled by Electric Locomotives".
# SU/MU/TU = single, multiple (two) and triple units.
ELECTRIC_COLUMNS = [
    ("WAG-5", "dry", "SU"), ("WAG-5", "dry", "MU"), ("WAG-5", "dry", "TU"),
    ("WAG-5", "wet", "SU"), ("WAG-5", "wet", "MU"), ("WAG-5", "wet", "TU"),
    ("WAG-7", "dry", "SU"), ("WAG-7", "dry", "MU"), ("WAG-7", "wet", "SU"), ("WAG-7", "wet", "MU"),
    ("WAG-9", "dry", "SU"), ("WAG-9", "dry", "MU"), ("WAG-9", "wet", "SU"), ("WAG-9", "wet", "MU"),
    ("WAG-12", "dry", "SU"), ("WAG-12", "wet", "SU"),
]
ELECTRIC = [
    ("JSG-SBP-JSG", "1:100", "1761 3170 4755 1761 3170 4755 2698 4856 2374 4273 2912 5242 2539 4570 4651 2878"),
    ("SBP-TIG-SBP", "1:125", "2375 4275 6412 2212 3663 5495 3385 6093 2665 4797 3630 6534 2853 5135 5326 3322"),
    # One printed row covers both of these sections.
    ("SBP-TLHR-SBP", "1:150", "2593 4667 7001 2376 4277 6415 3942 7096 3153 5675 4249 7648 3370 6066 6000 3766"),
    ("TIG-RSD-TIG", "1:150", "2593 4667 7001 2376 4277 6415 3942 7096 3153 5675 4249 7648 3370 6066 6000 3766"),
    ("TIG-SPRD", "1:125", "2375 4275 6412 3374 3663 5495 3385 6093 2665 4797 3630 6534 28S3 5135 5326 3322"),
    ("SPRD-TIG", "1:100", "1761 3170 4755 1761 3170 4755 2698 4856 2374 4273.2 2912 5242 2539 4S70 4651 2878"),
]

NOTES = {
    ("TIG-SPRD", "WAG-5", "wet", "SU"): "printed 3374, above the dry value 2375; probably a misprint",
}


def tonnes(printed):
    """'28S3' -> 2853: the page has an S where a 5 belongs in two cells."""
    return float(printed.replace("S", "5")) if re.fullmatch(r"[\dS]+(\.\d+)?", printed) else None


def main():
    rows = []
    for section, gradient, curve, values in DIESEL:
        values = values.split()
        for i, loco in enumerate(DIESEL_LOCOS):
            for weather, printed in zip(("dry", "wet"), values[2 * i:2 * i + 2]):
                rows.append((section, "diesel", loco, weather, "SU", printed, gradient, curve, 177))
    for section, gradient, values in ELECTRIC:
        for (loco, weather, units), printed in zip(ELECTRIC_COLUMNS, values.split()):
            rows.append((section, "electric", loco, weather, units, printed, gradient, "", 178))

    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["section", "traction", "loco_type", "weather", "units", "max_load_tonnes",
                         "max_load_as_printed", "ruling_gradient", "degree_of_curve", "note", "source_file", "page"])
        for section, traction, loco, weather, units, printed, gradient, curve, page in rows:
            value = tonnes(printed)
            note = NOTES.get((section, loco, weather, units), "")
            if "S" in printed:
                note = f"printed {printed}; read as {value:g}"
            writer.writerow([section, traction, loco, weather, units, f"{value:g}", printed,
                             gradient, curve, note, SOURCE, page])
    print(f"  freight_loads.csv: {len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
