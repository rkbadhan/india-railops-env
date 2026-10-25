"""
Download Sambalpur division station diagrams (SD) and Station Working Rules
(SWR) listed on East Coast Railway's "SWR, SD & CRP of SBP DIVISION" page.

Usage:
    .venv/Scripts/python.exe fetch_sbp_station_docs.py

Files land in rail_data/infrastructure/station_diagrams/sbp/<CODE>_<SD|SWR>.pdf
with a manifest.csv beside them. Valid files already there are skipped.
"""

import csv
import hashlib
import html
import os
import re
import sys
import time
import urllib.parse

from fetch_rail_data import DELAY_SECONDS, check_kind, fetch, save

PAGE = "https://eastcoastrail.indianrailways.gov.in/view_section.jsp?lang=0&id=0,6,932,938"
OUT = os.path.join("rail_data", "infrastructure", "station_diagrams", "sbp")


def document_links(page_html):
    """(code, station name, SD|SWR, url) for each station row of the table.

    The third cell links the station diagram; the fourth the SWR, or for a
    few stations a cable route plan (CRP), which is skipped.
    """
    for row in re.findall(r"<tr.*?</tr>", page_html, re.S):
        cells = re.findall(r"<td.*?</td>", row, re.S)
        text = [" ".join(html.unescape(re.sub("<[^>]+>", " ", c)).split()) for c in cells]
        if len(cells) < 4 or not re.fullmatch(r"[A-Z]{2,5}", text[0]):
            continue
        for kind, cell in zip(("SD", "SWR"), cells[2:4]):
            m = re.search(r'href="([^"]+)"', cell)
            if not m or "uploads/files/" not in m.group(1) or m.group(1).endswith("/files/"):
                continue
            url = urllib.parse.urljoin(PAGE, html.unescape(m.group(1)))
            if "CRP" in url:
                continue
            yield text[0], text[1], kind, url


def main():
    os.makedirs(OUT, exist_ok=True)
    body, _ = fetch(PAGE)
    rows, seen = [], {}
    for code, name, kind, url in document_links(body.decode("utf-8", "replace")):
        n = seen[(code, kind)] = seen.get((code, kind), 0) + 1
        filename = f"{code}_{kind}{'' if n == 1 else f'_{n}'}.pdf"
        path = os.path.join(OUT, filename)
        print(f"[{code}] {filename}")
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, "rb") as f:
                data = f.read()
            if check_kind(data, filename) == "ok":
                rows.append([code, name, kind, filename, url, "exists", len(data), hashlib.sha256(data).hexdigest()])
                continue
        try:
            data, _ = fetch(url)
            status = check_kind(data, filename)
            if status == "ok":
                save(data, path)
            rows.append([code, name, kind, filename, url, status, len(data), hashlib.sha256(data).hexdigest()])
            print(f"    {status}, {len(data):,} bytes")
        except Exception as error:  # keep going; failures are listed in the manifest
            rows.append([code, name, kind, filename, url, f"failed: {error}", 0, ""])
            print(f"    FAILED: {error}")
        time.sleep(DELAY_SECONDS)

    with open(os.path.join(OUT, "manifest.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["station_code", "station_name", "kind", "file", "url", "status", "bytes", "sha256"])
        writer.writerows(rows)
    failed = [r for r in rows if r[5] not in ("ok", "exists")]
    print(f"\nDone: {len(rows) - len(failed)} of {len(rows)} files OK.")
    for r in failed:
        print(f"  {r[3]}: {r[5]}  {r[4]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
