"""
Extract CSVs from the Sambalpur division Working Time Table (ECoR, 2023).

Usage:
    .venv/Scripts/python.exe extract/sambalpur_wtt.py

Reads  rail_data/infrastructure/WTT_ECoR_Sambalpur_2023.pdf
Writes rail_data/processed/{sections,block_sections,stations,passenger_paths,train_totals}.csv

The downloaded PDF holds Chapters I-II only (pages 1-100). The goods-train
load tables (Chapter VII) are not in it; extract/sambalpur_freight_loads.py
builds freight_loads.csv from the 2026 WTT instead.
"""

import csv
import os
import re
import statistics
import sys
import warnings
from collections import defaultdict

warnings.filterwarnings("ignore")
import pdfplumber  # noqa: E402

PDF = os.path.join("rail_data", "infrastructure", "WTT_ECoR_Sambalpur_2023.pdf")
OUT = os.path.join("rail_data", "processed")

# Timetable pages by section and direction, from the contents page (p.3).
# UP/DN follow the WTT: e.g. JSG-TIG UP runs Jharsuguda -> Titlagarh.
PAGE_SECTIONS = {}
for first, last, section, direction in [
    (22, 29, "SBP-KPJG", "UP"), (30, 37, "SBP-KPJG", "DN"),
    (40, 51, "JSG-TIG", "UP"), (52, 63, "JSG-TIG", "DN"),
    (64, 64, "BLGR-BHPI", None),  # one DN table and one UP table on the page
    (66, 73, "TIG-SPRD", "UP"), (74, 81, "TIG-SPRD", "DN"),
    (82, 82, "JNRD-LJR", None),  # one DN table and one UP table on the page
    (84, 91, "TIG-LAE", "DN"), (92, 99, "TIG-LAE", "UP"),
]:
    for page in range(first, last + 1):
        PAGE_SECTIONS[page] = (section, direction)

GLANCE_PAGES = [21, 39, 65, 83]
ISRT_PAGE = 20

TRAIN_NO = re.compile(r"^\*?\d{5}$")
TIME = re.compile(r"^[,.]?(\d{1,2})[.,:]?(\d{2})(?:[.,:](\d{2}))?$")
NOTE = re.compile(r"(Pre|Xing|X-ing|Prec)[-\s.]*(\d{5})", re.I)
NUMBER = re.compile(r"^\d+(\.\d+)?$")
ALLOWANCE = re.compile(r"\d+(\.\d+)?\s*[CRPD]\b")  # e.g. 5D, 11C, 0.5P


def is_bold(word):
    return "Bold" in word["fontname"]


def cx(w):
    return (w["x0"] + w["x1"]) / 2


def cy(w):
    return (w["top"] + w["bottom"]) / 2


def lines_of(words, tol=2.0):
    """Group words into text lines (top to bottom), each line left to right."""
    out = []
    for w in sorted(words, key=cy):
        if out and abs(cy(w) - cy(out[-1][-1])) <= tol:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in out]


def text_of(words):
    return " ".join(" ".join(w["text"] for w in line) for line in lines_of(words)).strip()


def norm_time(token):
    m = TIME.match(token)
    if not m:
        return None
    hh, mm, ss = m.groups()
    if int(hh) > 23 or int(mm) > 59 or (ss is not None and int(ss) > 59):
        return None
    return f"{int(hh):02d}:{mm}" + (f":{ss}" if ss else "")


# ---------------------------------------------------------------- timetables

def page_words(page):
    words = page.extract_words(extra_attrs=["fontname", "size"], keep_blank_chars=False)
    return [w for w in words if w["upright"]]


def band_edges(page, bbox, xs):
    """y positions of row rules inside bbox.

    A rule counts when it crosses at least half of the given column x
    positions: this ignores the short rules some cells have between arrival
    and departure, and survives a rule missing under one column.
    """
    x0, top, x1, bottom = bbox
    edges = sorted((e for e in page.edges if e["orientation"] == "h" and top - 2 <= e["top"] <= bottom + 2),
                   key=lambda e: e["top"])
    groups = []
    for e in edges:
        if groups and e["top"] - groups[-1][-1]["top"] <= 1.5:
            groups[-1].append(e)
        else:
            groups.append([e])
    ys = []
    for group in groups:
        crossed = sum(any(e["x0"] - 1 <= x <= e["x1"] + 1 for e in group) for x in xs)
        if crossed >= len(xs) / 2:
            ys.append(group[0]["top"])
    return ys


def parse_table(page, bbox, words):
    """Parse one timetable grid. Returns dict or None if it isn't one."""
    x0, top, x1, bottom = bbox
    inside = [w for w in words if x0 - 1 <= cx(w) <= x1 + 1 and top - 1 <= cy(w) <= bottom + 1]
    numbers = [w for w in inside if TRAIN_NO.match(w["text"]) and is_bold(w)]
    if not numbers:
        return None
    first_row = min(cy(w) for w in numbers)
    numbers = sorted((w for w in numbers if abs(cy(w) - first_row) < 3), key=cx)
    centers = [cx(w) for w in numbers]
    gap = (centers[1] - centers[0]) if len(centers) > 1 else 50
    bounds = [centers[0] - gap / 2] + [(a + b) / 2 for a, b in zip(centers, centers[1:])] + [centers[-1] + gap / 2]

    def column(w):
        for i in range(len(centers)):
            if bounds[i] <= cx(w) < bounds[i + 1]:
                return i
        return None

    # A rule can be missing under one train (e.g. above an arrow), so use every column.
    ys = band_edges(page, bbox, centers)
    bands = list(zip(ys, ys[1:]))
    in_band = lambda w, b: b[0] <= cy(w) < b[1]  # noqa: E731

    # Header: from the train-number band down to the band holding "H M S".
    start = next(i for i, b in enumerate(bands) if b[0] <= first_row < b[1])
    hms = next(i for i, b in enumerate(bands) if i >= start and any(
        in_band(w, b) and w["text"] in ("HMS", "H", "HMS.") and column(w) is not None for w in inside))
    header_bands = bands[start:hms + 1]

    trains = []
    for i, w in enumerate(numbers):
        cells = [text_of([v for v in inside if in_band(v, b) and column(v) == i]) for b in header_bands]
        cells = [c for c in cells if c and c not in ("HMS", "H M S")]
        mps = next((c.split()[0] for c in cells[1:] if re.match(r"^\d{2,3}(/\d{2,3})?\b", c)), "")
        fields = [c for c in cells[1:] if not re.match(r"^\d{2,3}(/\d{2,3})?\b", c)]
        fields += [""] * (4 - len(fields))
        trains.append({"train_no": w["text"].lstrip("*"), "suspended": w["text"].startswith("*"),
                       "from": fields[0], "to": fields[1], "name": fields[2], "days": fields[3], "mps": mps})

    # Body: tall bands are stations (arrival and departure lines), short ones
    # sit between stations (inter-station km, allowances). The short bands
    # after the last station are the section totals.
    body = [b for b in bands[hms + 1:] if b[1] - b[0] > 4]  # skip slivers between double rules
    if not body:
        return None
    heights = [b[1] - b[0] for b in body]
    gap_height = sorted(heights)[len(heights) // 4]  # most short bands are gaps between stations
    tall = [h > 1.5 * gap_height for h in heights]
    if not any(tall):
        return None
    last_station = max(i for i, t in enumerate(tall) if t)

    left = [w for w in inside if cx(w) < bounds[0]]
    right = [w for w in inside if cx(w) >= bounds[-1]]

    # Left-hand pages have seven ruled columns before the trains: km from
    # origin, km from far end, intermediate km, facing-point speed, station
    # code, station name, a/d. Right-hand pages only have the code, on the right.
    # A rule is sometimes missing or doubled on one row, so vote across rows.
    votes = defaultdict(int)
    for b, t in zip(body, tall):
        if not t:
            continue
        y = (b[0] + b[1]) / 2
        xs = sorted(e["x0"] for e in page.edges if e["orientation"] == "v"
                    and e["top"] <= y <= e["bottom"] and e["x0"] < centers[0] - 8)
        rules = []
        for x in xs:
            if not rules or x - rules[-1] > 2.5:
                rules.append(x)
        votes[tuple(round(x) for x in rules)] += 1
    rules = max(votes, key=votes.get)
    is_left_page = len(rules) == 8
    if not is_left_page and len(rules) > 2:
        print(f"  warning: p{page.page_number} has {len(rules)} left-hand rules; treating as a right-hand page")
    left_cols = dict(zip(["km", "km_other", "km_between", "fp_speed", "code", "name", "ad"],
                         zip(rules, rules[1:]))) if is_left_page else {}

    def left_cell(name, band):
        x_lo, x_hi = left_cols[name]
        return [w for w in left if in_band(w, band) and x_lo <= cx(w) < x_hi]

    # Some pages print the code column before the speed column; tell them apart by content.
    if is_left_page:
        stations_bands = [b for b, t in zip(body, tall) if t]
        digits = sum(NUMBER.match(w["text"]) is not None for b in stations_bands for w in left_cell("code", b))
        letters = sum(w["text"].isalpha() for b in stations_bands for w in left_cell("code", b))
        if digits > letters:
            left_cols["code"], left_cols["fp_speed"] = left_cols["fp_speed"], left_cols["code"]

    # Re-cut the body, also counting the km column (left pages) or the code
    # column (right pages), whose cells span a whole station row. Some pages
    # rule arrival from departure across every train column; such a rule cuts
    # through the station's km and code, so drop rules that cut a word there.
    if is_left_page:
        anchors = [sum(left_cols["km"]) / 2]
        anchor_words = [w for w in left if any(lo <= cx(w) < hi for lo, hi in
                                              (left_cols["km"], left_cols["code"]))]
    else:
        anchors = [(bounds[-1] + x1) / 2]
        anchor_words = right
    body_top = header_bands[-1][1]
    ys = [y for y in band_edges(page, bbox, centers + anchors) if y >= body_top - 1
          and not any(w["top"] + 1 < y < w["bottom"] - 1 for w in anchor_words)]
    if not ys or ys[0] > body_top + 2:
        ys.insert(0, body_top)
    body = [b for b in zip(ys, ys[1:]) if b[1] - b[0] > 4]

    # Height was a first guess; a station row is one with a code, name or km.
    # (Allowances that wrap onto two lines make some gaps as tall as stations.)
    # Everything from the first "Total ..." row down is footer.
    footer_y = float("inf")
    if is_left_page:
        for b in body:
            # Station info can overhang the rule (e.g. "E1" under Kerejanga), so search, don't anchor.
            if re.search(r"\b(Total|Refer)\b", text_of([w for w in left if in_band(w, b)])):
                footer_y = b[0]
                break

    def is_station(i, band):
        if band[0] >= footer_y:
            return False
        if is_left_page:
            return any(left_cell(c, band) for c in ("code", "km"))
        return any(in_band(w, band) for w in right)

    # Rarely two stations share a row with no rule between them (IBS above
    # BMCK on pp.78-79): split where the km (or code) cell has two lines.
    split_body = []
    for i, b in enumerate(body):
        if is_station(i, b):
            stacked = lines_of([w for w in left_cell("km", b) if NUMBER.match(w["text"])]) if is_left_page else \
                [ln for ln in lines_of([w for w in right if in_band(w, b)])]
            if len(stacked) == 2 and (is_left_page or stacked[1][0]["text"].upper() != "CABIN"):
                mid = (cy(stacked[0][0]) + cy(stacked[1][0])) / 2
                split_body += [(b[0], mid), (mid, b[1])]
                continue
        split_body.append(b)
    body = split_body
    tall =[is_station(i, b) for i, b in enumerate(body)]
    if not any(tall):
        return None
    last_station = max(i for i, t in enumerate(tall) if t)

    def header_label(name):
        """Column heading such as 'From SBP'; printed rotated on most pages."""
        x_lo, x_hi = left_cols[name]
        y_lo, y_hi = top, body[0][0]
        upright = [w for w in left if x_lo <= cx(w) < x_hi and y_lo <= cy(w) < y_hi and w["text"] != "KM"]
        if upright:
            return text_of(upright)
        chars = [c for c in page.chars if not c["upright"] and x_lo <= (c["x0"] + c["x1"]) / 2 < x_hi
                 and y_lo <= c["top"] < y_hi]
        by_x = defaultdict(list)
        for c in chars:
            by_x[round(c["x0"])].append(c)
        return " ".join("".join(c["text"] for c in sorted(cs, key=lambda c: -c["top"])).strip()
                        for _, cs in sorted(by_x.items()))

    km_labels = (header_label("km"), header_label("km_other")) if is_left_page else ("", "")

    def row_cells(band):
        return [text_of([v for v in inside if in_band(v, band) and column(v) == j]) for j in range(len(centers))]

    # The gap after the last station still belongs to the timetable (its
    # allowances carry on to the next page). Left pages label the footer;
    # on right pages it starts at the first row without allowance codes.
    if is_left_page:
        end = sum(1 for b in body if b[0] < footer_y)
    else:
        end = last_station + 1
        while end < len(body):
            cells = row_cells(body[end])
            if any(cells) and not any(ALLOWANCE.search(c) for c in cells):
                break
            end += 1

    stations, pending = [], None
    for i, b in enumerate(body[:end]):
        if not tall[i]:
            # Between-stations band: intermediate km on the left, allowances per train.
            km = [w for w in left_cell("km_between", b) if NUMBER.match(w["text"])] if is_left_page else []
            allow =[text_of([v for v in inside if in_band(v, b) and column(v) == j]) for j in range(len(centers))]
            # A gap can be split by a partial rule; join the pieces.
            if stations:
                prev = stations[-1]
                prev["after_km"] = prev["after_km"] or (km[0]["text"] if km else "")
                prev["after_allow"] = [f"{a} {b}".strip() for a, b in zip(prev["after_allow"], allow)]
            else:
                pending = [f"{a} {b}".strip() for a, b in zip(pending or [""] * len(allow), allow)]
            continue
        st = {"code": "", "name": "", "info": "", "km": "", "km_other": "", "fp_speed": "",
              "cells": [], "after_km": "", "after_allow": [""] * len(centers)}
        if is_left_page:
            st["code"] = text_of(left_cell("code", b))
            st["km"] = text_of(left_cell("km", b))
            st["km_other"] = text_of(left_cell("km_other", b))
            st["fp_speed"] = text_of(left_cell("fp_speed", b))
            name_lines = lines_of(left_cell("name", b))
            st["name"] = " ".join(w["text"] for w in name_lines[0]) if name_lines else ""
            st["info"] = " ".join(" ".join(w["text"] for w in ln) for ln in name_lines[1:])
            marks = left_cell("ad", b)
            a = [cy(w) for w in marks if w["text"] == "a"]
            d = [cy(w) for w in marks if w["text"] == "d"]
            split = (a[0] + d[0]) / 2 if a and d else (b[0] + b[1]) / 2
        else:
            st["code"] = text_of([w for w in right if in_band(w, b)])
            split = (b[0] + b[1]) / 2
        for j in range(len(centers)):
            cell = [v for v in inside if in_band(v, b) and column(v) == j]
            arr = [v for v in cell if cy(v) < split]
            dep = [v for v in cell if cy(v) >= split]
            st["cells"].append((text_of(arr), text_of(dep)))
        stations.append(st)

    # Pages that end a section carry three totals rows (Engg. allowance,
    # recovery, path/diversion) before "Refer Page No."; other pages only the latter.
    if is_left_page:
        footer = [row_cells(b) for b in body[end:] if "Total" in text_of([w for w in left if in_band(w, b)])]
        totals = footer[:3]
    else:
        footer = [cells for cells in map(row_cells, body[end:]) if any(cells)]
        totals = footer[:3] if len(footer) >= 3 else []
    return {"trains": trains, "stations": stations, "totals": totals, "pending": pending,
            "is_left": is_left_page, "km_labels": km_labels, "top": top}


def table_label(words, table_top):
    """Direction and title printed just above a table."""
    above = [w for w in words if table_top - 45 < w["bottom"] <= table_top + 1]
    text = text_of(above).upper()
    direction = "UP" if re.search(r"\bUP\b|\bP TRAINS", text) else "DN" if re.search(r"\bDN\b|DOWN", text) else None
    return direction, text


def parse_timetables(pdf):
    paths, totals, station_rows, problems = [], [], [], []
    for pno, (section, page_dir) in sorted(PAGE_SECTIONS.items()):
        page = pdf.pages[pno - 1]
        words = page_words(page)
        tables = sorted(page.find_tables(), key=lambda t: t.bbox[1])
        for seq, table in enumerate(tables):
            parsed = parse_table(page, table.bbox, words)
            if not parsed:
                continue
            label_dir, _ = table_label(words, table.bbox[1])
            direction = page_dir or label_dir
            if page_dir and label_dir and label_dir != page_dir:
                problems.append(f"p{pno}: label says {label_dir}, contents says {page_dir}")
            parsed.update(page=pno, table=seq, section=section, direction=direction)
            yield parsed


def assemble(tables):
    """Turn parsed tables into per-train station rows, carrying allowances forward."""
    # Right-hand pages have no station names; borrow from the left page of the spread.
    by_page = {(t["page"], t["table"]): t for t in tables}
    for t in tables:
        if t["is_left"]:
            continue
        mate = by_page.get((t["page"] - 1, t["table"]))
        if mate and mate["is_left"] and len(mate["stations"]) == len(t["stations"]):
            for s, m in zip(t["stations"], mate["stations"]):
                if not s["code"]:
                    s["code"] = m["code"]
                s["name"], s["info"], s["km"] = m["name"], m["info"], m["km"]
                s["fp_speed"], s["after_km"] = m["fp_speed"], m["after_km"]
        elif mate:
            print(f"  warning: p{t['page']} has {len(t['stations'])} station rows, "
                  f"p{mate['page']} has {len(mate['stations'])}; names not copied")

    rows, train_meta, train_totals = [], {}, []
    carry = {}     # (section, direction, train) -> allowance text waiting for the next station
    last_row = {}  # (section, direction, train) -> that train's latest row
    for t in tables:
        for j, train in enumerate(t["trains"]):
            key = (t["section"], t["direction"], train["train_no"])
            meta = train_meta.setdefault(key, dict(train))
            meta["suspended"] = meta["suspended"] or train["suspended"]
            for field in ("from", "to", "name", "days", "mps"):
                if not meta.get(field) and train.get(field):
                    meta[field] = train[field]
            if t["pending"] and t["pending"][j]:
                carry[key] = (carry.get(key, "") + " " + t["pending"][j]).strip()
            for s in t["stations"]:
                arr, dep = s["cells"][j]
                notes = sorted(set(f"{'Pre' if m.group(1).lower().startswith('pre') else 'Xing'} {m.group(2)}"
                                   for m in NOTE.finditer(f"{arr} {dep} {carry.get(key, '')}")))
                prev = last_row.get(key)
                if prev and s["code"] and prev["station_code"] == s["code"]:
                    # A junction ending one page is repeated at the top of the next: merge.
                    for field, value in (("arrival", first_time(arr)), ("departure", first_time(dep)),
                                         ("arrival_raw", arr), ("departure_raw", dep)):
                        prev[field] = prev[field] or value
                    prev["allowance_codes"] = ",".join(filter(None, [prev["allowance_codes"],
                                                                     clean_allowance(carry.pop(key, ""))]))
                else:
                    last_row[key] = {
                        "train_no": train["train_no"], "train_name": meta["name"], "from_station": meta["from"],
                        "suspended": meta["suspended"],
                        "to_station": meta["to"], "days": meta["days"], "mps_kmph": meta["mps"],
                        "section": t["section"], "direction": t["direction"],
                        "station_code": s["code"], "station_name": s["name"], "km": s["km"],
                        "arrival": first_time(arr), "departure": first_time(dep),
                        "arrival_raw": arr, "departure_raw": dep,
                        "allowance_codes": clean_allowance(carry.pop(key, "")),
                        "crossing_or_precedence_note": "; ".join(notes),
                        "page": t["page"],
                    }
                    rows.append(last_row[key])
                after = s["after_allow"][j] if j < len(s["after_allow"]) else ""
                if after:
                    carry[key] = after
            if t["totals"]:
                vals = [row[j] if j < len(row) else "" for row in t["totals"]]
                vals += [""] * (3 - len(vals))
                # The third row is printed "P/D", e.g. "44/05" = 44 min path, 5 min diversion.
                pd = re.match(r"^(\d+)\s*/\s*(\d+)", vals[2])
                train_totals.append({"train_no": train["train_no"], "section": t["section"],
                                     "direction": t["direction"], "engg_allowance_min": vals[0],
                                     "recovery_min": vals[1], "path_min": pd.group(1) if pd else "",
                                     "diversion_min": pd.group(2) if pd else "", "path_diversion_as_printed": vals[2],
                                     "page": t["page"]})
    # Metadata, including a suspension mark, can be supplied by a later page.
    for r in rows:
        meta = train_meta[(r["section"], r["direction"], r["train_no"])]
        r["train_name"], r["from_station"], r["to_station"] = meta["name"], meta["from"], meta["to"]
        r["days"], r["mps_kmph"] = meta["days"], meta["mps"]
        r["suspended"] = meta["suspended"]
    return rows, train_totals


def first_time(cell):
    # Some times are typeset with spaces ("1 6 . 00") or stray punctuation (";11.12").
    # Match one time at a time so adjacent values ("19.00 19.20") stay separate.
    # A comma can separate whole times. Only a colon or dot introduces seconds.
    pattern = r"(?<!\w)(?:\d(?:\s*\d)?\s*[.,:]\s*\d\s*\d(?:\s*[:.]\s*\d\s*\d)?|\d{3,4})(?!\w)"
    for match in re.finditer(pattern, cell):
        t = norm_time(re.sub(r"\s+", "", match.group()))
        if t:
            return t
    return ""


def clean_allowance(text):
    text = re.sub(r"\s*,\s*", ",", text.strip())
    return re.sub(r"\s+", ",", text)


# ------------------------------------------------------------ other pages

def parse_isrt(pdf):
    """Inter Section Running Time page: block sections, distance, running time."""
    page = pdf.pages[ISRT_PAGE - 1]
    words = page_words(page)
    mid = page.width / 2
    rows = []
    for panel in (lambda w: cx(w) < mid, lambda w: cx(w) >= mid):
        pw = [w for w in words if panel(w)]
        dn_x = statistics.median(w["x0"] for w in pw if w["text"] == "Block" and
                                 any(v["text"] == "Block" and v["x0"] < w["x0"] - 50 and abs(cy(v) - cy(w)) < 2 for v in pw))
        section, speed = None, ""
        for line in lines_of(pw):
            text = " ".join(w["text"] for w in line)
            m = re.match(r"^([A-Z]+)-+([A-Z]+)-+([A-Z]+)$", text)
            if m:
                section = f"{m.group(1)}-{m.group(2)}"
                continue
            m = re.search(r"Speed\s*-\s*([\d/]+)", text)
            if m:
                speed = m.group(1)
                continue
            # Each line holds up to two (block section, km, minutes) triples: UP then DN.
            triples = [line[i:i + 3] for i in range(0, len(line), 3)]
            if len(triples) == 1:
                tagged = [("UP" if triples[0][0]["x0"] < dn_x - 30 else "DN", triples[0])]
            else:
                tagged = list(zip(("UP", "DN"), triples))
            for direction, part in tagged:
                toks = [w["text"] for w in part]
                if len(toks) == 3 and re.match(r"^[A-Z]+-+[A-Z]+$", toks[0]) and NUMBER.match(toks[1]):
                    a, b = re.split(r"-+", toks[0])
                    rows.append({"section": section, "direction": direction, "from_station": a, "to_station": b,
                                 "distance_km": toks[1], "running_time_min": toks[2],
                                 "max_speed_kmph": speed.split("/")[0], "max_speed_as_printed": speed,
                                 "page": ISRT_PAGE})
                elif len(toks) == 3 and toks[0] == "TOTAL":
                    rows.append({"section": section, "direction": direction, "from_station": "TOTAL",
                                 "to_station": "", "distance_km": toks[1], "running_time_min": toks[2],
                                 "max_speed_kmph": "", "max_speed_as_printed": "", "page": ISRT_PAGE})
    return rows


GLANCE_FIELDS = {
    "1": "section_and_gauge", "2": "electrified", "3": "controlled", "4": "line_type",
    "5": "length", "6": "block_system", "7": "signalling", "8": "mps",
}

# The glance pages describe several sections in free text, so this table is
# transcribed by hand from them; build_sections() checks every length (and the
# line and block wording) against the extracted page text.
# section, glance page, length as printed, line_type, block_system
SECTIONS = [
    ("SBP-KPJG", 21, "142.7", "double", "Absolute Block; Double Line Lock & Block Instrument with LVCD"),
    ("JSG-TIG", 39, "230.5", "double", "Absolute Block; Double Line Lock & Block Instrument with LVCD"),
    ("IB-JSGR", 39, "8.7", "single", "Absolute Block; Tokenless Block Instrument with LVCD"),
    ("SLRA-SBPY", 39, "3.7", "single", "Absolute Block; Tokenless Block Instrument with LVCD"),
    ("BLGR-BHPI", 39, "15.5", "single", "Absolute Block; Tokenless Block Instrument with LVCD"),
    ("TIG-SPRD", 65, "130.3", "triple TIG-LJR, double LJR-SPRD",
     "Absolute Block; Double Line Lock & Block Instrument with LVCD; TIG-LJR 3rd line UFSBI without TLBI"),
    ("JNRD-LJR", 65, "55.4", "single",
     "Absolute Block with LVCD; UFSBI without TLBI LJR-BWIP, UFSBI with TLBI BWIP-JNRD"),
    ("TIG-LAE", 83, "174.1", "double", "Absolute Block; Double Line Lock & Block Instrument with LVCD"),
]


ISRT_NAMES = {"IB-JSGR": "JSGR-IB"}  # the running-time page names this one the other way round


def build_sections(glance, isrt):
    by_page = {g["page"]: g for g in glance}
    # UP length: the printed TOTAL, or the single row of a one-block section.
    isrt_km = {r["section"]: r["distance_km"] for r in isrt if r["direction"] == "UP" and r["from_station"] == "TOTAL"}
    isrt_km.update({r["section"]: r["distance_km"] for r in isrt
                    if r["direction"] == "UP" and r["section"] not in isrt_km and r["from_station"] != "TOTAL"})
    rows = []
    for section, page, length, line_type, block in SECTIONS:
        g = by_page[page]
        assert length in g["length"], f"{section}: {length} not in p{page} text {g['length']!r}"
        for word in re.findall(r"Single|Double|Triple", line_type.title()):
            assert word in g["line_type"], f"{section}: {word} not in p{page} line text"
        for word in ("Tokenless", "Lock & Block", "UFSBI"):
            if word in block:
                assert word in g["block_system"], f"{section}: {word} not in p{page} block text"
        rows.append({
            "section": section, "length_km": length, "length_km_isrt": isrt_km.get(ISRT_NAMES.get(section, section), ""),
            "line_type": line_type, "block_system": block,
            "electrified": "yes" if "Electric" in g["electrified"] and "Non" not in g["electrified"] else g["electrified"],
            "signalling": g["signalling"], "page": page,
        })
    return rows


def parse_glance(pdf):
    """'Section at a Glance' pages: one raw row per page, values as printed."""
    rows = []
    for pno in GLANCE_PAGES:
        text = pdf.pages[pno - 1].extract_text()
        items, current = {}, None
        for line in text.splitlines():
            m = re.match(r"^(\d{1,2})\s+(.*)$", line.strip())
            if m and m.group(1) in GLANCE_FIELDS and ":" in line:
                current = GLANCE_FIELDS[m.group(1)]
                items[current] = line.split(":", 1)[1].strip()
            elif m and int(m.group(1)) > 8:
                current = None
            elif current and line.strip() and "SECTION AT A GLANCE" not in line:
                items[current] += " " + line.strip().lstrip(":").strip()
        rows.append({"page": pno, **{k: items.get(k, "") for k in GLANCE_FIELDS.values()}})
    return rows


def to_float(text):
    try:
        return float(text)
    except ValueError:
        return None


def station_table(tables):
    """One row per station (or IBS signal post) and section, from the left-hand pages.

    Distances are railway chainages (e.g. km from Howrah); km_from_origin is
    the distance from the section's UP starting station. DN pages print the
    chainage columns the other way round, so for them the column that agrees
    with the UP pages is used. IBS posts differ between the UP and DN lines.
    """
    left = [t for t in tables if t["is_left"]]
    rows = []
    for section in dict.fromkeys(t["section"] for t in left):
        up = [t for t in left if t["section"] == section and t["direction"] == "UP"]
        dn = [t for t in left if t["section"] == section and t["direction"] == "DN"]
        origin = to_float(up[0]["stations"][0]["km"])
        up_km = {s["code"]: to_float(s["km"]) for t in up for s in t["stations"] if s["code"] != "IBS"}

        def dn_column(t):
            # DN pages: whichever chainage column matches the UP pages for shared stations.
            score = {col: sum(1 for s in t["stations"] if s["code"] in up_km and up_km[s["code"]] is not None
                              and to_float(s[col]) is not None and abs(to_float(s[col]) - up_km[s["code"]]) < 0.05)
                     for col in ("km", "km_other")}
            return max(score, key=score.get)

        found = {}  # station code, or chainage for IBS posts and unnamed rows -> row
        for t in up + dn:
            col = "km" if t["direction"] == "UP" else dn_column(t)
            for s in t["stations"]:
                chainage = to_float(s[col])
                if chainage is None or not (s["code"] or s["name"]):
                    continue
                # DN pages sometimes round the chainage differently (14.0 vs 14.015),
                # so named stations match on code; IBS posts differ by line, so on chainage.
                code = " ".join(s["code"].upper().split())
                key = code if code and code != "IBS" else round(chainage, 2)
                if code.startswith("BPSL"):
                    key = "BPSL CABIN"  # printed "BPSL" on the DN pages
                if key in found:
                    if t["direction"] not in found[key]["direction"]:
                        found[key]["direction"] = "both"
                    continue
                name, info = s["name"], s["info"]
                m = re.match(r"^(IBS)[,\s]+(.+)$", name)  # IBS rows print their info on the name line
                if m:
                    name, info = m.group(1), m.group(2)
                fixed = re.sub(r"\b(I{1,3}|IV)\s*-?\s*R\b", r"\1 R", info)
                cls = re.search(r"\(\s*([ABC])\s*\)", info)
                std = re.search(r"\b(IV|I{1,3})( R)?\b", fixed)
                interlocking = re.findall(r"\b(RRI|PI|SSI|EI|E1|RI|NI)\b", info)
                found[key] = {
                    "station_code": " ".join(s["code"].upper().split()), "station_name": name,
                    "section": section, "direction": t["direction"],
                    "km_from_origin": round(abs(chainage - origin), 3) if origin is not None else "",
                    "chainage_km": s[col],
                    "station_class": cls.group(1) if cls else
                    "PH" if "PH" in name + info else "SPL" if "SPL" in info else "",
                    "signalling_standard": (std.group(1) + (std.group(2) or "")) if std else "",
                    "interlocking": ("EI" if interlocking[0] == "E1" else interlocking[0]) if interlocking else "",
                    "signalling": "MACLS" if re.search(r"MACH?L\s*S", info) else "",
                    "speed_over_facing_points": s["fp_speed"], "as_printed": info, "page": t["page"],
                }
        rows += sorted(found.values(), key=lambda r: r["km_from_origin"])
    return rows


def write(name, rows):
    path = os.path.join(OUT, name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"  {name}: {len(rows)} rows")


def main():
    os.makedirs(OUT, exist_ok=True)
    with pdfplumber.open(PDF) as pdf:
        print(f"{PDF}: {len(pdf.pages)} pages")
        tables = list(parse_timetables(pdf))
        paths, train_totals = assemble(tables)
        write("passenger_paths.csv", paths)
        write("train_totals.csv", train_totals)
        write("stations.csv", station_table(tables))
        isrt = parse_isrt(pdf)
        write("block_sections.csv", isrt)
        write("sections.csv", build_sections(parse_glance(pdf), isrt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
