"""
High Density Network sections -> rail_data/processed/hdn_sections.csv

Usage:
    .venv/Scripts/python.exe extract/hdn.py

Reads rail_data/infrastructure/HDN_line_capacity_Dec2024.pdf (Railway Board,
Planning Directorate, status as on Dec 2024). Each corridor (HDN 1-7) has a
title page followed by section tables. Merged cells (railway, division,
route, group, line arrangement, system of working) are printed once per
group, so they are carried down to the rows below. The section count per
corridor is checked against the summary on page 12, and route km against
page 6.
"""

import csv
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")
import pdfplumber  # noqa: E402

PDF = os.path.join("rail_data", "infrastructure", "HDN_line_capacity_Dec2024.pdf")
OUT = os.path.join("rail_data", "processed", "hdn_sections.csv")

# Page 12, "Line Capacity Utilisation status ... (section-wise)": sections per corridor in
# each utilisation band (<80, 80-100, 100-120, 120-150, >150 %). It counts only sections
# with a utilisation figure, so HDN 3's eight Mumbai suburban through-line rows are not in it.
BANDS_PER_HDN = {1: [2, 9, 22, 11, 4], 2: [5, 2, 15, 16, 8], 3: [2, 3, 10, 2, 0], 4: [1, 4, 12, 4, 19],
                 5: [7, 6, 7, 17, 1], 6: [1, 8, 8, 7, 0], 7: [6, 9, 7, 1, 1]}
SECTIONS_PER_HDN = {h: sum(b) for h, b in BANDS_PER_HDN.items()}
# Page 6, "HDN - Route kilometer summary": total route km per corridor.
ROUTE_KM_PER_HDN = {1: 1421.93, 2: 2038.77, 3: 1320.53, 4: 1874.92, 5: 2040.67, 6: 1116.85, 7: 1237.56}

CARRY = ("railway", "division", "route", "group", "line_arrangement", "system_of_working")
FIELDS = ["sl_no", "railway", "division", "route", "section", "group", "length_km", "line_arrangement",
          "system_of_working", "charted_capacity_each_way", "avg_trains_each_way", "utilisation_pct",
          "works_in_progress"]


def clean(cell):
    return " ".join((cell or "").split())


def number(text):
    m = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return m.group(0) if m else ""


# Header words -> field. Pages have 13 or 14 columns, so map by header, not position.
HEADER_KEYS = [
    ("sl_no", ("S. No", "S.No")), ("railway", ("Rly",)), ("division", ("Div",)),
    ("group", ("Group",)), ("route", ("Route",)), ("section", ("Section",)), ("length_km", ("Length",)),
    ("line_arrangement", ("arrang",)), ("system_of_working", ("System",)),
    ("charted_capacity_each_way", ("Charted",)), ("avg_trains_each_way", ("Average",)),
    ("utilisation_pct", ("utilisation",)), ("works_in_progress", ("doubling", "works")),
]


LINE_TYPES = [(r"\bTSL\b", "twin single"), (r"\bSL\b", "single"), (r"\bDL\b|Double", "double"),
              (r"\bTL\b|Tripp?le|3rd", "triple"), (r"\bQL\b|Quad", "quadruple"), (r"Penta", "penta"),
              (r"Hexa", "hexa")]


def line_type(printed):
    """'QL' -> 'quadruple'; a section printed with several arrangements -> 'mixed'."""
    found = {name for pattern, name in LINE_TYPES if re.search(pattern, printed, re.I)}
    if "twin single" in found:
        found.discard("single")
    return found.pop() if len(found) == 1 else "mixed" if found else ""


def header_map(header):
    mapping = {}
    for i, cell in enumerate(header):
        for field, keys in HEADER_KEYS:
            if field not in mapping.values() and any(k.lower() in cell.lower() for k in keys):
                mapping[i] = field
                break
    return mapping


def main():
    rows, hdn, counts = [], None, {}
    with pdfplumber.open(PDF) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            title = re.match(r"\s*HDN\s*[–-]\s*(\d)\s*\(", text)
            if title:
                hdn, carried = int(title.group(1)), {}
                corridor = " ".join(text.split("Planning Directorate")[0].split())
                continue
            if hdn is None:
                continue
            for table in page.extract_tables():
                cells0 = [clean(c) for c in table[0]]
                if not any("Charted" in c for c in cells0):
                    continue
                mapping = header_map(cells0)
                for raw in table[1:]:
                    cells = [clean(c) for c in raw]
                    row = {field: "" for field in FIELDS}
                    for i, field in mapping.items():
                        if i < len(cells):
                            row[field] = cells[i]
                    row["sl_no"] = re.sub(r"\D", "", row["sl_no"])  # e.g. "\13"
                    if not row["sl_no"]:
                        # A section broken over two rows: join it to the row above.
                        if rows and row["section"] and not row["length_km"] and rows[-1]["page"] == pno:
                            rows[-1]["section"] = f"{rows[-1]['section']} {row['section']}".strip()
                        continue
                    for field in CARRY:
                        if row[field]:
                            carried[field] = row[field]
                        else:
                            row[field] = carried.get(field, "")
                    row.update(hdn=hdn, hdn_corridor=corridor, page=pno)
                    rows.append(row)
                    counts[hdn] = counts.get(hdn, 0) + 1

    out = []
    for r in rows:
        out.append({
            "hdn": r["hdn"], "hdn_corridor": r["hdn_corridor"], "sl_no": r["sl_no"],
            "railway": r["railway"], "division": r["division"], "route": r["route"], "section": r["section"],
            "group": r["group"], "length_km": number(r["length_km"]), "line_type": line_type(r["line_arrangement"]),
            "line_arrangement": r["line_arrangement"],
            "system_of_working": r["system_of_working"],
            "charted_capacity_each_way": number(r["charted_capacity_each_way"]),
            "avg_trains_each_way": number(r["avg_trains_each_way"]),
            "utilisation_pct": number(r["utilisation_pct"]),
            "capacity_as_printed": r["charted_capacity_each_way"], "trains_as_printed": r["avg_trains_each_way"],
            "utilisation_as_printed": r["utilisation_pct"], "works_in_progress": r["works_in_progress"],
            "page": r["page"],
        })
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        writer.writeheader()
        writer.writerows(out)

    rated = [r for r in out if r["utilisation_pct"]]
    print(f"  hdn_sections.csv: {len(out)} rows, {len(rated)} with a utilisation figure "
          f"(page 12 counts {sum(SECTIONS_PER_HDN.values())})")
    ok = True
    for h in sorted(SECTIONS_PER_HDN):
        mine = [r for r in rated if r["hdn"] == h]
        bands = [0] * 5
        for r in mine:
            u = float(r["utilisation_pct"])
            # Page 12 puts a section at exactly 100 % in "100-120", so bands include their lower edge.
            bands[0 if u < 80 else 1 if u < 100 else 2 if u < 120 else 3 if u < 150 else 4] += 1
        km = sum(float(r["length_km"] or 0) for r in out if r["hdn"] == h)
        flag = "" if len(mine) == SECTIONS_PER_HDN[h] else "  <-- count differs"
        ok &= not flag
        band_note = "" if bands == BANDS_PER_HDN[h] else f" (page 12 bands {BANDS_PER_HDN[h]})"
        print(f"    HDN {h}: {counts.get(h, 0):3d} rows, {len(mine):3d} rated (page 12: {SECTIONS_PER_HDN[h]}); "
              f"bands {bands}{band_note}; {km:8.2f} km (page 6: {ROUTE_KM_PER_HDN[h]}){flag}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
