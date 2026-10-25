"""
Download the public data sources for the Indian rail dispatch RL environment.

Usage (Windows, macOS or Linux, Python 3.9+):
    pip install certifi                  # needed for the indianrailways.gov.in sites
    python fetch_rail_data.py            # download everything
    python fetch_rail_data.py --list     # only print what would be downloaded
    python fetch_rail_data.py --only infrastructure freight

Files land in ./rail_data/<layer>/ and every attempt is logged in
./rail_data/manifest.csv (url, status, bytes, sha256). Valid files that already
exist are skipped, so you can re-run it safely after a failure; files that
turned out to be error pages are downloaded again. The exit code is 1 if
anything still needs attention.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import certifi
except ImportError:
    certifi = None

OUT_DIR = "rail_data"
# An honest name; Overpass rejects (406) requests that pretend to be a browser.
USER_AGENT = "railway-rl-env-data-fetch/0.1 (research; python-urllib)"
DELAY_SECONDS = 2  # pause between requests, to be polite to the servers

# (layer, filename or None to use the URL's own name, url, what it is)
SOURCES = [
    # 1. Track, stations, running times
    ("infrastructure", "WTT_ECoR_Sambalpur_2023.pdf",
     "https://eastcoastrail.indianrailways.gov.in/uploads/files/1697628048596-Time%20Table.pdf",
     "Working Time Table, Sambalpur division: block sections, running times, booked paths"),
    ("infrastructure", "WTT_ECoR_Sambalpur_2026.pdf",
     "https://eastcoastrail.indianrailways.gov.in/uploads/files/1768821366372-Railway%20book%20Sambalpur.pdf",
     "Working Time Table, Sambalpur division, wef 01.01.2026 (about 90 MB)"),
    ("infrastructure", "HDN_line_capacity_Dec2024.pdf",
     "https://rb.indianrailways.gov.in/uploads/directorate/planning/High%20Density%20Network%20in%20Indian%20Railways.pdf",
     "237 busiest sections: length, line type, capacity, trains per day, utilisation"),
    ("infrastructure", "NWR_section_at_a_glance.pdf",
     "https://nwr.indianrailways.gov.in/uploads/files/1742970384051-6-section%20at%20a%20glance.pdf",
     "North Western Railway section summary"),
    ("infrastructure/station_diagrams", "ECoR_SWR_Jharmunda.pdf",
     "https://eastcoastrail.indianrailways.gov.in/uploads/files/1740642050728-SWR%20of%20JHARMUNDA.pdf",
     "Station Working Rules diagram"),
    ("infrastructure/station_diagrams", "ECoR_SWR_WMY_AB_cabin.pdf",
     "https://eastcoastrail.indianrailways.gov.in/uploads/files/1719289727420-WMY_A_B_CABIN06062024.pdf",
     "Station Working Rules diagram"),
    ("infrastructure/station_diagrams", "ECR_SWR_79_NRN.pdf",
     "https://ecr.indianrailways.gov.in/cris//uploads/files/1704450811303-79.%20NRN.pdf",
     "Station Working Rules diagram, Samastipur division"),
    ("infrastructure/station_diagrams", "ECR_SWR_78_ARG.pdf",
     "https://ecr.indianrailways.gov.in/cris//uploads/files/1704450787490-78.%20ARG.pdf",
     "Station Working Rules diagram"),
    ("infrastructure/station_diagrams", "ECR_SWR_84_DSKG.pdf",
     "https://ecr.indianrailways.gov.in/cris//uploads/files/1696920094497-84.%20DSKG.pdf",
     "Station Working Rules diagram"),
    ("infrastructure/station_diagrams", "ECR_SWR_92_SBV.pdf",
     "https://ecr.indianrailways.gov.in/cris//uploads/files/1704451083585-92.%20SBV.pdf",
     "Station Working Rules diagram"),
    ("infrastructure/station_diagrams", "ECR_SWR_31A_VKNR.pdf",
     "https://ecr.indianrailways.gov.in/cris//uploads/files/1696919892207-31A.%20VKNR%20PH-I%20pdf.pdf",
     "Station Working Rules diagram"),
    ("infrastructure/station_diagrams", "ECR_SWR_26_HIR.pdf",
     "https://ecr.indianrailways.gov.in/cris//uploads/files/1724743004842-26.HIR%20FINAL%20PH.pdf",
     "Station Working Rules diagram"),
    ("infrastructure/station_diagrams", "ECR_SWR_8_PPA.pdf",
     "https://ecr.indianrailways.gov.in/cris//uploads/files/1739186157828-8.PPA%20PH-1%20DL.pdf",
     "Station Working Rules diagram"),

    # 2. Passenger timetables
    ("timetables/datameet", "stations.json",
     "https://raw.githubusercontent.com/datameet/railways/master/stations.json",
     "Station coordinates, zone, state (CC0, ~2016)"),
    ("timetables/datameet", "trains.json",
     "https://raw.githubusercontent.com/datameet/railways/master/trains.json",
     "Train routes (CC0, ~2016)"),
    ("timetables/datameet", "schedules.json",
     "https://raw.githubusercontent.com/datameet/railways/master/schedules.json",
     "Stop-by-stop schedules (CC0, ~2016)"),

    # 3. Freight
    ("freight", "CR_goods_sheds.pdf",
     "https://cr.indianrailways.gov.in/cris//uploads/files/1739513488573-List%20of%20Goods%20Sheds.pdf",
     "Where freight loads and unloads, Central Railway"),
    ("freight", "CR_goods_sheds_2.pdf",
     "https://cr.indianrailways.gov.in/cris//uploads/files/1747739651236-goods%20shed.pdf",
     "Goods sheds, Central Railway (second list)"),
    ("freight", "CR_goods_sidings.pdf",
     "https://cr.indianrailways.gov.in/cris//uploads/files/1747829073806-Goods-Siding.pdf",
     "Private sidings, Central Railway"),
    ("freight", "RB_TMS_FOIS_premises_classification_2024.pdf",
     "https://rb.indianrailways.gov.in/uploads/directorate/traffic_comm/Rates-Letters/2024/System%20(TMS%20FOIS)%20based%20classification%20of%20Railway%20premises%20%20(goods%20shed%2C%20station%20etc_)%20dated%2023_07_2024.pdf",
     "Railway Board classification of freight premises"),
    ("freight", "RB_freight_stock_targets_2024-25.pdf",
     "https://rb.indianrailways.gov.in/uploads/directorate/mec_engg/downloads/freight/2024/Freight%20Stock%20Performance%20Target%20for%202024-25.pdf",
     "Wagon turnaround and freight stock targets"),
    ("freight", "ECR_freight_statistics.pdf",
     "https://ecr.indianrailways.gov.in/uploads/files/1730199735136-Freight%20Statistics.pdf",
     "East Central Railway freight statistics"),
    # rsdebate.nic.in now redirects to sansad.in. Not yet found there:
    # session 265 unstarred Q1391 (02.08.2024) and Q583 (26.07.2024).
    ("freight", "RS_PQ_Feb2024.pdf",
     "https://sansad.in/getFile/annex/263/AU908.pdf?source=pqars",
     "Rajya Sabha unstarred Q908 (09.02.2024): average speed of passenger and freight trains"),
    ("freight", "ISB_thesis_freight_traffic_management_2026.pdf",
     "https://eprints.exchange.isb.edu/id/eprint/2458/1/Essays%20on%20Freight%20Traffic%20Management%20in%20Indian%20Railways.pdf",
     "How section controllers hold and release freight (real IR data)"),

    # 4. Rules
    ("rules", "SER_GSR_2011.pdf",
     "https://ser.indianrailways.gov.in/cris/uploads/files/1606473656907-G&SR%202011%20Full.pdf",
     "General & Subsidiary Rules, South Eastern Railway"),
    ("rules", "SCR_GSR.pdf",
     "https://scr.indianrailways.gov.in/cris//uploads/files/1421322916862-G%20&%20SR%20Upto%20AS14.pdf",
     "General & Subsidiary Rules, South Central Railway"),
    ("rules", "SECR_Operating_Manual_2025.pdf",
     "https://secr.indianrailways.gov.in/uploads/files/1760593351226-Operating%20Manual%202025.pdf",
     "Operating manual: train precedence, control procedures"),
    ("rules", "RB_SR_chapter5_station_working.pdf",
     "https://rb.indianrailways.gov.in/uploads/directorate/safety/SR_SR/SR_SR_CHAP5.PDF",
     "Control and working of stations"),

    # 5. Research papers and benchmarks
    ("research", "DISPLIB_2025.pdf",
     "https://arxiv.org/pdf/2509.12254",
     "DISPLIB: common format and benchmark for train dispatching"),
    ("research/displib", "displib_spec_2025-09-17.pdf",
     "https://displib.github.io/displib_spec_2025-09-17.pdf",
     "DISPLIB problem and file format specification"),
    ("research/displib", "displib_problems_2025-09-17.zip",
     "https://displib.github.io/displib_problems_2025-09-17.zip",
     "DISPLIB: all problem instances (JSON)"),
    ("research/displib", "displib_solutions_2025-09-17.zip",
     "https://displib.github.io/displib_solutions_2025-09-17.zip",
     "DISPLIB: best known solutions (JSON)"),
    ("research/displib", "displib_instances_testing.zip",
     "https://displib.github.io/displib_instances_testing.zip",
     "DISPLIB: small test instances"),
    ("research/displib", "displib_verify_v0_3.zip",
     "https://displib.github.io/displib_verify_v0_3.zip",
     "DISPLIB solution verification program (Python)"),
    ("research/displib", "displib_index.html",
     "https://displib.github.io/",
     "DISPLIB home page, with the table of instance sizes and best known objectives"),
    ("research", "RSTGCN_Indian_delays_2025.pdf",
     "https://arxiv.org/pdf/2510.01262",
     "Nationwide Indian train delay dataset paper"),
    ("research", "Indian_delays_zero_shot_markov_2018.pdf",
     "https://arxiv.org/pdf/1806.02825",
     "135 Indian trains, two years of running status"),
    ("research", "IITB_zero_based_timetable_preprint.pdf",
     "https://easychair.org/publications/preprint/4FgM/open",
     "IIT Bombay + IR zero-based timetabling"),
    ("research", "RL_single_track_rescheduling_Leeds_2024.pdf",
     "https://eprints.whiterose.ac.uk/id/eprint/217651/1/1-s2.0-S2210970624000532-main.pdf",
     "Q-learning train rescheduling, single track"),
]

# OpenStreetMap rail data for the Sambalpur division area (tracks, stations,
# switches, sidings), fetched through the public Overpass API.
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# south, west, north, east: covers Sambalpur division, including Titlagarh-Lakholi
# west to 81.8 E and Titlagarh-Singapur Road south to 19.2 N.
SAMBALPUR_BBOX = "19.2,81.8,22.0,85.3"
OVERPASS_QUERY = f"""
[out:json][timeout:180];
(
  way["railway"~"^(rail|siding)$"]({SAMBALPUR_BBOX});
  node["railway"~"^(station|halt|switch|signal|crossing|junction)$"]({SAMBALPUR_BBOX});
);
out body;
>;
out skel qt;
"""


def make_ssl_context():
    # Certificates stay verified. The indianrailways.gov.in sites chain to
    # eMudhra's emSign root, which is in certifi but often missing from the
    # Windows store, and the ECoR server only offers an RSA key-exchange
    # cipher that Python's default cipher list excludes.
    if certifi is None:
        print("Note: certifi is not installed; indianrailways.gov.in downloads will likely fail "
              "(pip install certifi).")
        context = ssl.create_default_context()
    else:
        context = ssl.create_default_context(cafile=certifi.where())
    context.set_ciphers("DEFAULT")
    return context


OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=make_ssl_context()))


def safe_name(name):
    name = urllib.parse.unquote(name)
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or "download"


def quote_url(url):
    parts = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parts.path, safe="/%&()=+,;-_.~")
    query = urllib.parse.quote(parts.query, safe="%&=+,;:/?()-_.~")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def fetch(url, data=None, retries=3, timeout=120):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                quote_url(url),
                data=data,
                headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            )
            with OPENER.open(request, timeout=timeout) as response:
                return response.read(), response.headers.get("Content-Type", "")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            last_error = error
            print(f"    attempt {attempt} failed: {error}")
            time.sleep(DELAY_SECONDS * attempt)
    raise last_error


def save(body, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(body)


def check_kind(body, filename, *, expected=None):
    if expected not in (None, "osm"):
        raise ValueError(f"unknown expected data schema: {expected}")
    if filename.lower().endswith(".pdf") and not body.startswith(b"%PDF"):
        return "not_a_pdf"  # usually an error page or a login wall
    if filename.lower().endswith(".json") or expected == "osm":
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return "not_json"
        if not isinstance(payload, (dict, list)):
            return "not_json"
        if expected == "osm":
            if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
                return "not_osm_json"
            # Overpass can return HTTP 200 with partial elements and a runtime error.
            if payload.get("remark"):
                return "overpass_error"
    if filename.lower().endswith(".zip") and not body.startswith(b"PK"):
        return "not_a_zip"
    return "ok"


MANIFEST_HEADER = ["layer", "file", "url", "status", "bytes", "sha256", "what_it_is"]
OSM_SOURCE = ("infrastructure/osm", "sambalpur_rail_osm.json", OVERPASS_URL,
              "OSM tracks, stations, switches, sidings")


def load_manifest(path):
    # Keyed by (layer, file) so a partial run (--only) updates its own rows
    # and keeps everyone else's.
    rows = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) == len(MANIFEST_HEADER):
                    rows[(row[0], row[1])] = row
    return rows


def download(layer, filename, url, note, data=None, timeout=120, expected=None):
    """Return (manifest row, whether a request was made)."""
    path = os.path.join(OUT_DIR, layer, filename)
    print(f"[{layer}] {filename}")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as f:
            body = f.read()
        if check_kind(body, filename, expected=expected) == "ok":
            print("    already downloaded, skipping")
            return [layer, filename, url, "exists", len(body), hashlib.sha256(body).hexdigest(), note], False
        print("    existing file is not valid, downloading again")
    try:
        body, _ = fetch(url, data=data, timeout=timeout)
        status = check_kind(body, filename, expected=expected)
        save(body, path)
        print(f"    {status}, {len(body):,} bytes")
        return [layer, filename, url, status, len(body), hashlib.sha256(body).hexdigest(), note], True
    except Exception as error:  # keep going; failures are listed in the manifest
        print(f"    FAILED: {error}")
        return [layer, filename, url, f"failed: {error}", 0, "", note], True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="print sources and exit")
    parser.add_argument("--only", nargs="*", help="only these layers, e.g. infrastructure freight")
    parser.add_argument("--skip-osm", action="store_true", help="skip the OpenStreetMap download")
    args = parser.parse_args()

    sources = SOURCES
    if args.only:
        sources = [s for s in SOURCES if s[0].split("/")[0] in args.only]

    if args.list:
        for layer, filename, url, note in sources:
            print(f"[{layer}] {filename}\n    {note}\n    {url}")
        print(f"[osm] sambalpur_rail_osm.json (Overpass, bbox {SAMBALPUR_BBOX})")
        return

    os.makedirs(OUT_DIR, exist_ok=True)
    manifest_path = os.path.join(OUT_DIR, "manifest.csv")
    manifest = load_manifest(manifest_path)
    rows = []

    for layer, filename, url, note in sources:
        filename = safe_name(filename or url.rstrip("/").split("/")[-1])
        row, requested = download(layer, filename, url, note)
        rows.append(row)
        if requested:
            time.sleep(DELAY_SECONDS)

    if not args.skip_osm and (not args.only or "osm" in args.only or "infrastructure" in args.only):
        layer, filename, url, note = OSM_SOURCE
        row, _ = download(layer, filename, url, note,
                          data=urllib.parse.urlencode({"data": OVERPASS_QUERY}).encode(), timeout=300, expected="osm")
        rows.append(row)

    # Drop rows for sources that have since been removed from SOURCES.
    known = {(layer, safe_name(filename or url.rstrip("/").split("/")[-1])) for layer, filename, url, _ in SOURCES}
    known.add(OSM_SOURCE[:2])
    manifest = {key: row for key, row in manifest.items() if key in known}
    for row in rows:
        manifest[(row[0], row[1])] = row

    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(MANIFEST_HEADER)
        writer.writerows(manifest.values())

    ok = sum(1 for r in rows if r[3] in ("ok", "exists"))
    print(f"\nDone: {ok} of {len(rows)} files OK. Details in {manifest_path}")
    failed = [r for r in rows if r[3] not in ("ok", "exists")]
    if failed:
        print("Needs attention:")
        for r in failed:
            print(f"  {r[1]}: {r[3]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
