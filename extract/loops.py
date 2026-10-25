"""
Loop lines and home signals -> rail_data/processed/{loops,home_signals}.csv

Usage:
    .venv/Scripts/python.exe extract/loops.py

Sources:
- Sambalpur division Station Working Rules (SWR) in
  rail_data/infrastructure/station_diagrams/sbp/ (fetch_sbp_station_docs.py):
  section 2.5.1 "Running lines ... holding capacity in CSL" is parsed
  from the PDF text.
- Sambalpur division station diagrams (SD) in the same folder, for stations
  without an SWR: read off the rendered drawings and transcribed below. Where
  a diagram has a text layer, every transcribed length is checked against it
  when the script runs; scanned diagrams (no text layer) are marked as such.
- Station Working Rule diagrams from other divisions (Waltair yard, seven
  Samastipur-division stations), transcribed the same way, as examples.
- WTT_ECoR_Sambalpur_2026.pdf (scanned, no text layer): "Speed over loop
  lines" (p.139) and "Location of home signal posts" (pp.164-165).

Lengths are CSL (clear standing length) in metres, between the signals or
marks given in length_basis (STR = starter, SH = shunt signal, SS = stop
signal, SB = stop board, DE = dead end).
"""

import csv
import os
import re
import warnings

warnings.filterwarnings("ignore")
import pymupdf  # noqa: E402

DIAGRAMS = os.path.join("rail_data", "infrastructure", "station_diagrams")
SBP_DOCS = os.path.join(DIAGRAMS, "sbp")
WTT = "WTT_ECoR_Sambalpur_2026.pdf"
OUT = os.path.join("rail_data", "processed")

# Sambalpur division station diagrams (stations without an SWR), read off the
# rendered drawing: code -> [(line, type as printed, length as printed, basis, note)].
SBP_DIAGRAM_LINES = {
    "AMB": [("1", "common loop", "749", "STR-STR", ""), ("2", "UP main", "798", "STR-SH", ""),
            ("3", "DN main", "938", "STR-SH", ""), ("4", "common loop", "857", "STR-STR", ""),
            ("5", "common line", "710", "STR-STR", ""), ("6", "common line", "710", "STR-STR", ""),
            ("7", "3rd main line", "955", "STR-STR", ""), ("8", "common 3rd line", "858", "STR-STR", ""),
            ("9", "common line", "820", "STR-STR", "")],
    "ATS": [("1", "UP loop", "744.4", "STR-stop signal", ""), ("2", "UP main", "767", "STR-stop signal", ""),
            ("3", "DN main", "777.06", "STR-stop signal", ""), ("4", "DN loop", "746", "STR-stop signal", "")],
    "BAMR": [("1", "common loop", "744", "STR-STR", ""), ("2", "UP main", "778", "SS-STR", ""),
             ("3", "DN main", "1001", "SS-STR", ""), ("4", "common loop", "916", "STR-STR", ""),
             ("5", "common loop", "744", "STR-STR", ""), ("6", "common loop", "718", "STR-STR", "")],
    "BLGR": [("1", "common loop", "715", "STR-SH", ""), ("2", "DN main", "742", "STR-SH", ""),
             ("3", "UP main", "854", "STR-SH", ""), ("4", "common loop", "774", "STR-STR", ""),
             ("5", "common loop", "1086", "STR-STR", "")],
    "BMCK": [("1", "common loop", "702", "STR-STR", ""), ("2", "UP main", "729.7", "STR-SS", ""),
             ("3", "DN main", "737", "STR-SH", ""), ("4", "DN loop", "737", "STR-SS", ""),
             ("5", "3rd main line / common loop", "767", "STR-STR", ""),
             ("6", "3rd common loop line", "739", "STR-STR", "")],
    "BONA": [("1", "common loop", "743", "STR-STR", ""), ("2", "UP main", "728", "STR-SS", ""),
             ("3", "DN main", "835", "STR-SS", ""), ("4", "common loop", "796", "STR-STR", "")],
    "BRGA": [("1", "common loop", "775", "STR-STR", ""), ("2", "DN main", "791", "STR-SH", ""),
             ("3", "UP main", "888", "STR-SH", ""), ("4", "common loop", "823", "STR-STR", ""),
             ("5", "common loop", "1453", "STR23-SH38", "also the UP longer loop: 835 m STR23-STR34"),
             ("6", "common goods loop", "733", "STR-STR", "")],
    "BRPL": [("1", "common loop", "743", "STR-STR", ""), ("2", "DN main", "755", "STR-SH", ""),
             ("3", "UP main", "982", "STR-SH", ""), ("4", "common loop", "800", "STR-STR", ""),
             ("5", "common loop", "769", "STR-STR", "")],
    "BUDM": [("1", "common loop", "756", "STR-STR", ""), ("2", "DN main", "788", "STR-SH", ""),
             ("3", "UP main", "969", "STR-SS", ""), ("4", "UP loop / DN reception line", "797", "STR-SH", ""),
             ("5", "UP loop / DN reception line", "751", "STR-SH", "")],
    "BWIP": [("1", "loop line", "728.6", "STR-STR", ""), ("2", "main line", "776.6", "STR-STR", "")],
    "CHAR": [("1", "common loop", "756", "STR-STR", ""), ("2", "common loop", "790", "STR-STR", ""),
             ("3", "UP main", "1018", "SH-STR", ""), ("4", "DN main", "1595", "STR-SH", ""),
             ("5", "common loop", "1535", "STR-STR", "")],
    "DFR": [("1", "DN loop", "727", "STR-SS", ""), ("2", "DN main", "766", "STR-SS", ""),
            ("3", "UP main", "779", "STR-stop signal", ""), ("4", "UP loop", "736", "STR-SH", "")],
    "DJX": [("1", "common loop", "752", "STR-STR", ""), ("2", "DN main", "771", "STR-stop signal", ""),
            ("3", "UP main", "922", "STR-stop signal", "printed 'UP LOOP', but it is the through UP line"),
            ("4", "common loop", "778", "STR-STR", ""), ("5", "common loop", "752", "STR-STR", "")],
    "DKLU": [("1", "common loop", "865", "STR-STR", ""), ("2", "UP main", "850", "STR-SH", ""),
             ("3", "DN main", "874", "STR-SH", ""), ("4", "DN loop", "884", "STR-SH", ""),
             ("5", "3rd main line", "1590", "S29-S40", ""),
             ("6", "loop line / longer loop", "869", "S31-S24", "longer loop 1464 m S31-S36"),
             ("7", "loop line cum ballast line", "869", "STR-STR", "")],
    "DOBH": [("1", "common loop", "749", "STR-STR", ""), ("2", "UP main", "776", "STR-stop signal", ""),
             ("3", "DN main", "772", "STR16-stop signal 19", ""),
             ("4", "DN loop", "749", "STR14-stop signal 21", "DN long haul loop 1467 m STR14-stop signal 13")],
    "GBQ": [("1", "common loop", "751.80", "STR-STR", ""), ("2", "UP main", "774", "STR-stop signal", ""),
            ("3", "DN main", "757.25", "STR-stop signal", ""), ("4", "DN loop", "727.50", "STR-stop signal", "")],
    "HATB": [("1", "common loop", "738", "STR-STR", ""), ("2", "DN main", "844", "SH-STR", ""),
             ("3", "UP main", "777", "STR-SS", ""), ("4", "UP loop", "754", "STR-SS", "")],
    "HKG": [("1", "DN loop", "796", "STR-SH", ""), ("2", "DN main", "821", "STR-stop signal", ""),
            ("3", "UP main", "1155", "STR-SH", ""), ("4", "common loop", "933", "STR-STR", ""),
            ("5", "common loop", "756", "STR-STR", ""), ("6", "goods loop / common loop", "726", "STR-STR", "")],
    "HNPA": [("1", "common loop", "749", "S11-S18", "long haul loop 1484 m S7-S18"),
             ("2", "UP main", "758.5", "STR-SH", ""), ("3", "DN main", "856", "STR-SH", ""),
             ("4", "common loop", "749", "STR-STR", ""), ("5", "common loop", "749", "STR-STR", "")],
    "JNRD": [("1", "loop line", "911", "STR-stop signal", ""), ("2", "main line", "916", "STR-stop signal", ""),
             ("3", "loop line", "811", "STR-stop signal", ""), ("4", "loop line", "769", "STR-stop signal", "")],
    "JRPD": [("1", "common loop", "905", "STR-STR", ""), ("2", "DN main", "967", "STR-SH", ""),
             ("3", "UP main", "1001", "STR-SH", ""), ("4", "common loop", "950", "STR-STR", "")],
    "JUJA": [("1", "common loop", "755", "STR-STR", ""), ("2", "UP main", "799", "STR-SH", ""),
             ("3", "DN main", "879", "STR-SH", ""), ("4", "common loop", "931", "STR-STR", "")],
    "KBJ": [("1", "common loop", "772", "STR-STR", ""), ("2", "DN main", "923", "STR-SH", ""),
            ("3", "UP main", "1080", "STR-SH", ""), ("4", "common loop", "849", "STR-STR", ""),
            ("5", "common loop", "757", "STR-STR", ""), ("6", "goods common loop", "769", "STR-STR", "")],
    "KDLR": [("1", "common loop", "760", "STR-STR", ""), ("2", "UP main", "733", "STR-stop signal", ""),
             ("3", "DN main", "725", "STR-stop signal", ""), ("4", "DN loop", "740", "STR-stop signal", ""),
             ("5", "3rd main line", "1032", "STR19-STR24", "1488 m STR19-STR12"),
             ("6", "long haul loop", "1032", "STR21-STR22", "1488 m STR21-STR10")],
    "KHPL": [("1", "DN loop", "752", "SS-STR", ""), ("2", "DN main", "777", "SS-STR", ""),
             ("3", "UP main", "791", "STR-SH", ""), ("4", "common loop", "754", "STR-STR", "")],
    "KSNG": [("1", "common loop", "730", "STR-STR", ""), ("2", "UP main", "760", "STR-SH", ""),
             ("3", "DN main", "754", "SH-STR", ""), ("4", "common loop", "718", "STR-STR", ""),
             ("5", "3rd main line", "902", "STR-STR", "")],
    # The LJR diagram numbers six lines but prints no lengths or line names.
    "LJR": [(str(n), "", "", "", "diagram shows the line but no length or name") for n in range(1, 7)],
    "LSX": [("1", "common loop", "772", "STR-STR", ""), ("2", "DN main", "793", "STR-SH", ""),
            ("3", "UP main", "794", "STR-SH", ""), ("4", "common loop", "757", "STR-STR", "")],
    "MANE": [("1", "DN loop", "759", "STR-SS", ""), ("2", "DN main", "789", "STR-SS", ""),
             ("3", "UP main", "804.5", "STR-SS", ""), ("4", "UP loop", "777", "STR-SS", "")],
    "NRLR": [("1", "common loop", "749", "STR-STR", ""), ("2", "UP main", "770", "STR-SH", ""),
             ("3", "DN main", "885.30", "STR-stop signal", ""), ("4", "DN loop", "873", "STR-stop signal", ""),
             ("5", "3rd main line", "757", "STR-STR", ""), ("6", "3rd loop line", "757", "STR-STR", "")],
    "RAIR": [("1", "UP loop", "789", "STR-SS", ""), ("2", "UP main", "760", "STR-SS", ""),
             ("3", "common loop", "1029", "STR-STR", ""), ("4", "DN main", "1170", "STR-SS", "")],
    "RPRD": [("1", "common loop", "729", "STR-STR", ""), ("2", "UP main", "821", "STR-SH", ""),
             ("3", "DN main", "775", "STR-SH", ""), ("4", "common loop", "751", "STR-STR", ""),
             ("5", "3rd main line", "745", "STR-STR", ""), ("6", "3rd loop line", "755", "STR-STR", "")],
    # Dense junction layout; each length sits on the row of its line's track circuit label.
    "SBP": [("1", "common loop", "735", "STR-STR", ""), ("2", "main line / common loop", "730", "STR-STR", ""),
            ("3", "main / common loop (TLHR)", "764", "STR-STR", ""),
            ("4", "main line JSG / common loop", "765", "STR-STR", ""),
            ("5", "main line JSG / goods common loop", "720", "STR-STR", ""),
            ("6", "goods common loop", "822", "STR-STR", ""), ("7", "common loop", "815", "STR-STR", "")],
    "SBPY": [("1", "common loop", "745", "STR-STR", ""), ("2", "DN main", "785", "STR-SS", ""),
             ("3", "UP main", "1040", "STR-SS", ""), ("4", "common loop", "880", "STR-STR", ""),
             ("5", "common loop", "739", "STR-STR", ""), ("6", "common loop", "720", "STR-STR", "")],
    "SFC": [("1", "common loop", "813", "STR-STR", ""), ("2", "DN main", "831", "STR-SH", ""),
            ("3", "UP main", "789", "STR-SS", ""), ("4", "UP loop", "757", "STR-SS", "")],
    "SFK": [("1", "DN loop", "744", "STR-SS", ""), ("2", "DN main", "765", "STR-SS", ""),
            ("3", "UP main", "941", "STR-SH", ""), ("4", "common loop", "800", "STR-STR", ""),
            ("5", "common loop", "729", "STR-STR", ""), ("6", "UP loop", "747", "STR-SS", "")],
    "SNPU": [("1", "loop 1", "754.200", "STR-STR", ""), ("2", "main line", "789", "STR-STR", ""),
             ("3", "loop 2", "789", "STR-STR", ""), ("4", "goods loop", "757", "STR-STR", "")],
    "SRGP": [("1", "common loop", "752", "STR-STR", ""), ("2", "UP main", "745", "SH-STR", ""),
             ("3", "DN main", "750", "SH-STR", ""), ("4", "common loop", "739", "STR-STR", "")],
    "THV": [("1", "common loop", "729", "STR-STR", ""), ("2", "UP main", "750", "STR-SB", ""),
            ("3", "DN main", "897", "STR-SB", ""), ("4", "common loop", "864", "STR-STR", "")],
    "TIG": [("1", "common loop", "853.5", "STR-STR", ""), ("2", "common loop", "858", "STR-STR", ""),
            ("3", "UP/DN main line", "1024", "STR-STR", ""), ("4", "UP/DN main line", "942.725", "STR-STR", ""),
            ("5", "common loop", "740", "STR-DP", ""), ("6", "common loop", "720", "STR-DP", ""),
            ("7", "common loop", "720", "STR-STR", ""), ("8", "common loop", "740", "STR-STR", "")],
}

# Other divisions, as examples. file, page, station code, name, zone/division,
# then (line, type, length as printed, basis).
STATIONS = [
    ("ECoR_SWR_WMY_AB_cabin.pdf", 7, "WMY", "Waltair Marshalling Yard A & B cabins", "ECoR/WAT", [
        ("1", "yard running line", "685", "CSL"),
        ("3", "yard running line", "1003", "CSL"),
        ("4", "yard running line", "1126", "CSL"),
        ("hump engine line", "yard running line", "1016", "CSL"),
        ("5", "yard running line", "1447", "CSL"),
        ("6", "yard running line", "1156", "CSL"),
        ("7", "yard running line", "1101", "CSL"),
        ("8", "yard running line", "985", "CSL"),
        ("9", "yard running line", "917", "CSL"),
        ("10", "yard running line", "794", "CSL"),
        ("11", "yard running line", "745", "CSL"),
        ("12", "yard running line", "671", "CSL"),
        ("13", "yard running line", "630", "CSL"),
    ]),
    ("ECR_SWR_26_HIR.pdf", 1, "HIR", "Harinagar", "ECR/SPJ", [
        ("1", "loop", "750.13", "CSL"),
        ("2", "UP main", "806.63", "CSL"),
        ("3", "DN main", "806.63", "CSL"),
        ("4", "loop", "766", "CSL"),
        ("5", "loop", "766", "CSL"),
        ("TM siding", "siding", "290", "CSL"),
    ]),
    ("ECR_SWR_31A_VKNR.pdf", 1, "VKNR", "Valmikinagar Road", "ECR/SPJ", [
        ("1", "loop", "738.0", "CSL"),
        ("2", "DN main", "696.0", "CSL"),
        ("3", "UP main", "755.50", "CSL"),
    ]),
    ("ECR_SWR_8_PPA.pdf", 1, "PPA", "Pipra", "ECR/SPJ", [
        ("1", "loop", "702.0", "CSL"),
        ("2", "DN main", "702.0", "CSL"),
        ("3", "UP main", "750.0", "CSL"),
    ]),
    ("ECR_SWR_78_ARG.pdf", 1, "ARG", "Angarghat", "ECR/SPJ", [
        ("1", "loop", "690.30", "CSL"),
        ("2", "main", "674.00", "CSL"),
        ("3", "loop", "689.00", "CSL"),
        ("siding", "siding", "", "length not shown"),
    ]),
    ("ECR_SWR_79_NRN.pdf", 1, "NRN", "Singhiyaghat", "ECR/SPJ", [
        ("1", "loop", "690.00", "CSL"),
        ("2", "main", "680.00", "CSL"),
        ("3", "loop", "692.00", "CSL"),
    ]),
    ("ECR_SWR_84_DSKG.pdf", 1, "DSKG", "Dr. Shri Krishna Singh Nagar Garhpura", "ECR/SPJ", [
        ("1", "loop", "710.00", "CSL"),
        ("2", "main", "686.00", "CSL"),
        ("3", "loop", "691.00", "CSL"),
    ]),
    ("ECR_SWR_92_SBV.pdf", 1, "SBV", "Simri Bakhtiarpur", "ECR/SPJ", [
        ("1", "loop", "686.00", "CSL"),
        ("2", "main", "686.00", "CSL"),
        ("3", "loop", "739.00", "CSL"),
    ]),
]

# WTT 2026 p.139 "Speed over loop lines": code as printed -> (name as printed, km/h).
# Two codes are misprinted on the page; the comment gives the code used elsewhere.
LOOP_SPEEDS = {
    "JSGR": ("Jharsuguda Road", 30), "BXQ": ("Brundamal", 30),  # printed "Brundamal(BUDM)"
    "LPG": ("Lapanga", 30), "RGL": ("Rengali", 30), "SSN": ("Sason", 30), "SLRA": ("Sarla", 30),
    "SBP": ("Sambalpur", 30), "HKG": ("Hirakud", 30), "GBQ": ("Godbhaga", 30), "ATS": ("Attabira", 30),
    "DOBH": ("Deobahal", 30),  # printed "Deobahal(DOBL)"
    "BRGA": ("Bargarh Road", 30), "BRPL": ("Barpali", 30), "DJX": ("Dungripali", 30),
    "KHPL": ("Khaliapali", 30), "LSX": ("Loisinga", 30), "BLGR": ("Balangir", 30),
    "DFR": ("Deogaon Road", 30), "SFC": ("Saintala", 30), "BUDM": ("Budmal", 30), "SFK": ("Sikir", 30),
    "ANMD": ("Arangmahanadi", 30), "BLSN": ("Belsonda", 30), "MSMD": ("Mahasamund", 30),
    "ARN": ("Arand", 30), "BMKJ": ("Bhimkhoj", 30), "BGBR": ("Bagbahara", 30), "KMK": ("Komakhan", 30),
    "KRAR": ("Khariar Road", 30), "NPD": ("Nawapara Road", 30), "LKNA": ("Lakhna", 30),
    "HSK": ("Harishankar Road", 30), "TRKR": ("Turekela Road", 30), "KBJ": ("Kantabanji", 30),
    "MRBL": ("Muribahal", 30), "RNBT": ("Rehenbhata", 30), "TIG": ("Titlagarh", 30),
    "KSNG": ("Kesinga", 30), "KDLR": ("Kandel Rd", 30), "RPRD": ("Rupra Rd", 30), "NRLR": ("Norla Rd", 30),
    "LJR": ("Langigarh Rd", 30), "AMB": ("Ambolala", 30), "DKLU": ("Doikalu", 30), "MNGD": ("Muniguda", 30),
    "BMCK": ("Bissamcuttack", 30), "THV": ("Theruvali", 30), "BWIP": ("Bhawanipatna", 30),
    "JNRD": ("Junagarh", 30), "SBPY": ("Sambalpur City", 30), "MANE": ("Maneswar", 30),
    "HATB": ("Hatibari", 30), "JUJA": ("Jujumura", 30), "CHAR": ("Charmal", 30), "RAIR": ("Rairakhol", 30),
    "BAMR": ("Bamur", 30), "SRGP": ("Saragipali", 30), "HNPA": ("Handapa", 30), "BONA": ("Boinda", 30),
    "JRPD": ("Jharpada", 30), "BHPI": ("Bichupalli", 30), "JRTB": ("Jhartarbha", 30),
    "NSGR": ("Narasinghgarh", 30), "SNPU": ("Sonepur", 30), "PNPI": ("Purunapani", 30),
    "JRMA": ("Jharmunda", 30), "BOUH": ("Boudh", 15), "BRSR": ("Biranarsinghpur", 30),
    "PUKK": ("Purunakatak", 30),
}

# WTT 2026 pp.164-165 "Location of home signal posts (UP & DN)", values as printed (km/post).
# station, number of home signals, UP home, DN home, third line UP home, third line DN home, remarks
HOME_SIGNALS = [
    ("JSGR", 3, "515/31-33", "517/12-14", "IBP 16N-17N (IB END). UP", "", ""),
    ("BXQ", 2, "519/17-19", "521/34-36", "", "", ""),
    ("BPSL", 2, "523/33-35", "524/24-26", "", "", ""),
    ("LPG", 2, "528/11-13", "530/28-30", "", "", ""),
    ("RGL", 2, "539/19-21", "541/20-22", "", "", ""),
    ("SSN", 2, "548/3-5", "549/34-36", "", "", ""),
    ("SLRA", 3, "554/11-13", "556/24-26 (SBP END)", "556/19-21 (SBPY END) DN SIDE.", "", ""),
    ("SBP", 3, "561/7-9 (JSG END), 561/9-11 (SBPY END)", "564/2-4", "", "", ""),
    ("HKG", 2, "568/41-43", "570/38-40", "", "", ""),
    ("GBQ", 2, "578/15-17", "579/42-580/2", "", "", ""),
    ("ATS", 2, "586/11-13", "587/40-42", "", "", ""),
    ("DOBH", 2, "592/11-13", "594/16-18", "", "", ""),
    ("BRGA", 2, "603/53-55", "606/14-16", "", "", ""),
    ("BRPL", 2, "623/11-13", "625/10-12", "", "", ""),
    ("DJX", 2, "637/29-31", "639/32-34", "", "", ""),
    ("KHPL", 2, "650/19-21", "652/8-10", "", "", ""),
    ("LSX", 2, "661/11-13", "663/16-18", "", "", ""),
    ("BLGR", 3, "679/15-17 (LSX END), 287/10-11 (BHPI END)", "682/2-4", "", "", ""),
    ("DFR", 2, "697/19-21", "699/6-8", "", "", ""),
    ("SFC", 2, "714/35-37", "716/30-32", "", "", ""),
    ("BUDM", 2, "723/27-29", "725/26-28", "", "", ""),
    ("BHPI", 2, "273/6-7", "275/1-2", "", "", ""),
    ("JRTB", 2, "262/16-17", "264/12-13", "", "", ""),
    ("NSGR", 2, "251/8-9", "253/9-10", "", "", ""),
    ("SNPU", 2, "240/16-17", "242/17-18", "", "", ""),
    ("PNPI", 2, "231/10-11", "233/11-12", "", "", ""),
    ("JRMA", 2, "213/2-3", "211/3-4", "", "", ""),
    ("BOUH", 2, "194/9-10", "196/16-17", "", "", "printed BOUDH"),
    ("BRSR", 2, "181/10-11", "183/6-7", "", "", ""),
    ("PUKK", 1, "", "169/19-20", "", "", ""),
    ("SFK", 2, "733/7-8", "735/1-2", "", "", ""),
    ("TIG", 4, "201/16-17 (RNBT side), 201/17M-19N (SFK end)", "203/38-40 (KSNG side)", "",
     "203/44A-42A (Dn Home) (KSNG side)", "3rd LINE"),
    ("KSNG", 4, "214/37-39", "216/29N-30N", "214/44A-46A", "216/32A-34A", "3rd LINE"),
    ("KDLR", 4, "223/11-13", "225/02-224/42", "222/24A-26A", "225/06A-04A", "3rd LINE"),
    ("RPRD", 4, "230/31-33", "232/40-38", "230/28A-30A", "232/44A-42A", "3rd LINE"),
    ("NRLR", 4, "237/25-27", "239/24-22", "237/24A1-30A", "239/22A-20A", "3rd LINE"),
    ("LJR", 5, "248/27-29, 1/03-05 (BWIP)", "250/34-32", "248/14A-15A", "251/06A-04A", "3RD LINE BWIP END."),
    ("AMB", 5, "261/29-31", "264/50-50A AMB/1162A-1164A (MVAA)", "261/26A-28A", "264/50A-48A",
     "3RD LINE VED SIDING"),
    ("DKLU", 4, "274/11N-13N", "276/18N-20", "273/24A-26A", "276/24A-22A", "3rd LINE"),
    ("MNGD", 4, "287/15-17", "289/16-14", "287/16A-18A", "289/26A-24A1", ""),
    ("BMCK", 3, "304/23-25", "306/08-06A", "304/14F-16F", "", ""),
    ("THV", 2, "322/21-23", "324/20-18", "", "", ""),
    ("BWIP", 2, "31/1-2", "29/9-10", "", "", ""),
    ("JNRD", 1, "...", "52/07-08", "", "", "TERMINAL STATION."),
    ("SBPY", 3, "4/43-45", "7/8-10", "558/7-9A (SLRA end)", "", ""),
    ("MANE", 2, "14/11-13", "15/42-16/2", "", "", ""),
    ("HATB", 2, "26/17-19", "28/8-10", "", "", ""),
    ("JUJA", 2, "38/3-5", "40/6-8", "", "", ""),
    ("CHAR", 2, "54/43-55/01", "57/46-48", "", "", ""),
    ("RAIR", 2, "70/01-03", "72/02-04", "", "", ""),
    ("BAMR", 2, "85/13-15", "87/16-14", "", "", ""),
    ("SRGP", 2, "95/1-3", "96/38-97/02", "", "", ""),
    ("HNPA", 2, "109/13-15", "111/16-18", "", "", ""),
    ("BONA", 2, "116/15-17", "118/26-24", "", "", ""),
    ("JRPD", 2, "131/33-35", "133/32-34", "", "", ""),
    ("RNBT", 2, "193/13-14", "195/8-9", "", "", ""),
    ("MRBL", 2, "183/19-21", "185/12-14", "", "", ""),
    ("KBJ", 2, "168/09-11", "170/08-10", "", "", ""),
    ("TRKR", 2, "154/17-19", "156/10-12", "", "", ""),
    ("HSK", 2, "145/19-21", "147/2-4", "", "", ""),
    ("LKNA", 2, "129/19-21", "131/16-18", "", "", ""),
    ("NPD", 2, "115/15-17", "118/2-4", "", "", ""),
    ("KRAR", 2, "104/9-11", "106/10-12", "", "", ""),
    ("KMK", 2, "93/13-15", "95/20-22", "", "", ""),
    ("BGBR", 2, "83/7-9", "84/30-32", "", "", ""),
    ("BMKJ", 2, "72/3-5", "73/34-36", "", "", ""),
    ("ARN", 2, "60/23-25", "62/24-26", "", "", ""),
    ("MSMD", 2, "52/13-15", "54/4-6", "", "", ""),
    ("BLSN", 2, "44/3-5", "45/32-34", "", "", ""),
    ("ANMD", 2, "34/21-23", "36/30-32", "", "", ""),
]


SWR_SECTION = re.compile(r"(?i)2\.5\.1\.?\s*(?:\(i\)\s*)?RUNNING\s+LINES")
SWR_LINE = re.compile(r"(?i)(Examination\s+line\s+No\.?\s*\d+|Loco\s+Reverse\s+Line|Line\s*No\.?\s*\d+)\.?\s*"
                      r"(.*?)\s*(\d{3,4}(?:\.\d+)?)\s*(?:M\b\.?|mtrs\.?|Mts\.?)\s*(\([^)]*\))?")


def parse_swr(path):
    """Running lines from SWR section 2.5.1: (page, [(line, type as printed, length, basis)])."""
    doc = pymupdf.open(path)
    pages = [" ".join(p.get_text().split()) for p in doc]
    for i in range(len(pages)):
        text = " ".join(pages[i:i + 2])  # a table can run onto the next page
        for m in SWR_SECTION.finditer(pages[i]):
            seg = text[m.start(): m.start() + 2500]
            # The heading itself says "direction of movements", so look for the end after it.
            end = re.search(r"2\.5\.2|2\.5\.1\.1|DIRECTION\s+OF\s+MOVEMENTS?\s*:", seg[60:], re.I)
            seg = seg[:60 + end.start()] if end else seg[:1500]
            lines = []
            for label, desc, length, basis in SWR_LINE.findall(seg):
                if label.lower().startswith("examination"):
                    label, desc = "examination " + re.sub(r"\D", "", label), "examination line"
                elif label.lower().startswith("loco"):
                    label, desc = "loco reverse", "loco reverse line"
                else:
                    label = re.sub(r"\D", "", label)
                lines.append((label, desc.strip(" ().-"), length, basis.strip("() ")))
            if lines:
                return i + 1, lines
    return None, []


def normal_type(printed):
    """'DNMain' -> 'DN main', 'Common Loop Line' -> 'common loop', '3rd main line' -> '3rd line main'."""
    p = printed.lower()
    if not p:
        return ""
    if "examination" in p or "reverse" in p:
        return p
    has_main = "main" in p
    has_loop = any(w in p for w in ("loop", "common line", "reception", "comm. line", "3rd line"))
    base = "main/loop" if has_main and has_loop else "main" if has_main else \
        ("goods loop" if "loop" in p else "goods line") if "goods" in p else "loop"
    squashed = p.replace(" ", "")
    side = "UP/DN " if squashed.startswith("up/dn") else "UP " if squashed.startswith("up") else \
        "DN " if squashed.startswith(("dn", "down")) else "common " if "common" in p and base != "main/loop" else ""
    third = "3rd line " if "3rd" in p else ""
    return f"{third}{side}{base}".strip()


def pdf_numbers(path, page=1):
    text = pymupdf.open(path)[page - 1].get_text()
    return ("CSL" in text.upper()), set(re.findall(r"\d+(?:\.\d+)?", text))


def check_lengths(path, page, lines):
    """Every transcribed length must appear in the diagram's text."""
    _, numbers = pdf_numbers(path, page)
    for line, _, length, _ in lines:
        assert not length or length in numbers, f"{os.path.basename(path)} line {line}: {length} not in text"


def write(name, header, rows):
    with open(os.path.join(OUT, name), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"  {name}: {len(rows)} rows")


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []

    def add(code, name, division, line, printed, length, basis, file, page, how, note=""):
        kind = normal_type(printed)
        speed = LOOP_SPEEDS.get(code, ("", ""))[1] if division == "ECoR/SBP" and "loop" in kind else ""
        rows.append([code, name, division, line, kind, printed, length, basis, speed,
                     "read" if length else "missing", file, page, how, note])

    # Sambalpur division: SWR text first, then the station diagram, else missing.
    for code, (name, speed) in LOOP_SPEEDS.items():
        swr = os.path.join(SBP_DOCS, f"{code}_SWR.pdf")
        page, lines = parse_swr(swr) if os.path.exists(swr) else (None, [])
        if lines:
            for line, printed, length, basis in lines:
                add(code, name, "ECoR/SBP", line, printed, length, basis, os.path.basename(swr), page,
                    "SWR section 2.5.1, parsed from PDF text")
            continue
        if code in SBP_DIAGRAM_LINES:
            sd = os.path.join(SBP_DOCS, f"{code}_SD.pdf")
            has_text, numbers = pdf_numbers(sd)
            for line, printed, length, basis, note in SBP_DIAGRAM_LINES[code]:
                if has_text and length:
                    assert length in numbers, f"{code} line {line}: {length} not in diagram text"
                how = ("read off the drawing; length checked against PDF text" if has_text else
                       "read off the scanned drawing (no text layer)")
                add(code, name, "ECoR/SBP", line, printed, length, basis, os.path.basename(sd), 1, how, note)
            continue
        rows.append([code, name, "ECoR/SBP", "", "loop", "", "", "", speed, "missing", WTT, 139,
                     "no SWR or station diagram found; WTT gives loop speed only", ""])

    for file, page, code, name, division, lines in STATIONS:
        check_lengths(os.path.join(DIAGRAMS, file), page, lines)
        for line, kind, length, basis in lines:
            add(code, name, division, line, kind, length, basis, file, page,
                "length from PDF text; main/loop read from the drawing")

    write("loops.csv", ["station_code", "station_name", "division", "line_no", "line_type",
                        "line_type_as_printed", "length_m", "length_basis", "loop_speed_kmph", "status",
                        "source_file", "page", "how_read", "note"], rows)

    write("home_signals.csv",
          ["station_code", "home_signals", "up_home_km", "dn_home_km", "third_line_up_home_km",
           "third_line_dn_home_km", "remarks", "source_file", "page"],
          [[*row, WTT, 164 if i < 35 else 165] for i, row in enumerate(HOME_SIGNALS)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
