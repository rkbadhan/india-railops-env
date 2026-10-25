"""
List Working Time Tables, Station Working Rules / station diagrams and
"at a glance" documents published on the Indian Railways zone websites.
Documents are listed, not downloaded.

Usage:
    .venv/Scripts/python.exe find_more_sources.py            # crawl all zones
    .venv/Scripts/python.exe find_more_sources.py WR SR      # re-crawl some, keep the rest
    .venv/Scripts/python.exe find_more_sources.py --check    # test the links (first 1 KB only)
    .venv/Scripts/python.exe find_more_sources.py --workers 4 # crawl up to four zones at once

Writes rail_data/processed/more_sources.csv
(zone, division, document, url, year, found_on, page, link_text, and link_check
after --check).

Each zone site is crawled from its home page, following only links whose
text looks like it leads to these documents, a few levels deep and a
limited number of pages per zone, with a pause between requests.
"""

import argparse
import csv
import html
import os
import posixpath
import re
import sys
import time
import urllib.error
import urllib.parse
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone

from fetch_rail_data import DELAY_SECONDS, fetch

ZONES = {
    "CR": "cr", "ER": "er", "ECR": "ecr", "ECoR": "eastcoastrail", "NCR": "ncr", "NER": "ner",
    "NFR": "nfr", "NR": "nr", "NWR": "nwr", "SCR": "scr", "SER": "ser", "SECR": "secr", "SR": "sr",
    "SWR": "swr", "WCR": "wcr", "WR": "wr",
}
MAX_PAGES = 120
MAX_DEPTH = 4
PAUSE = max(1, DELAY_SECONDS // 2)
OUT = os.path.join("rail_data", "processed", "more_sources.csv")
STATUS_OUT = os.path.join("rail_data", "processed", "crawl_status.csv")


@dataclass
class CrawlResult:
    rows: list
    pages_read: int
    errors: list
    pending_pages: int

    @property
    def complete(self):
        return self.pages_read > 0 and not self.errors and not self.pending_pages

    @property
    def status(self):
        if not self.pages_read:
            return "unavailable"
        if self.pending_pages:
            return "page_limit"
        if self.errors:
            return "partial"
        return "complete" if self.rows else "no_documents_found"

# What a document link is, judged from its link text and file name, else from the page it
# is on ("Time Table" pages list WTTs under the division's name; "Trains at a glance" is a
# passenger train list, not a section summary).
DOC_TYPES = [
    ("Working Time Table", r"working\s*time\s*-?\s*table|\bW\.?T\.?T\b"),
    ("Station Working Rules", r"station\s*working\s*rules?|\bS\.?W\.?R\b|\bSWRD?\b"),
    ("Station Diagram", r"station\s*diagram|mini\s*diagram|yard\s*plan|\bS\.?D\b|\bSIP\b|interlocking\s*plan"),
    ("Section/Division at a Glance", r"(?<!trains )at\s*a\s*glance|(?<!trains)ataglance|section\s*at\s*a"),
]
PAGE_TYPES = DOC_TYPES + [("Working Time Table", r"^\s*(working\s*)?time\s*table\s*$")]
# Files that sit on those pages but are something else.
NOT_A_DOCUMENT = re.compile(r"(?i)telephone|contact|tourist|tourism|public\s*interface|organi[sz]ation|"
                            r"org\.?\s*chart|\bRTI\b|history|charter|amenit|grievance|medical|map\b|"
                            r"corporate\s*goal|swachh|forms?\b|manual|workshop|\.(jpe?g|png|gif)$")
# Menu links worth following towards those documents: direct hits first, then menus.
STRONG = re.compile(r"(?i)working\s*time|\bWTT\b|time\s*table|station\s*working|\bSWR\b|station\s*diagram|"
                    r"at\s*a\s*glance|\bSD\b")
FOLLOW = re.compile(r"(?i)for\s*ir\s*personnel|operating|operations|division|downloads?|documents?|"
                    r"publications?")
FILE = re.compile(r"(?i)\.(pdf|zip|docx?|xlsx?)$|/uploads?/files/|/downloadfile(?:_\d+)?\.jsp$")
# Case-sensitive on purpose: "SBP DIVISION", "Khurda Road (KUR) Division", "Waltair Division".
DIVISION_WORDS = re.compile(r"\(([A-Z]{2,5})\)\s*[Dd][Ii][Vv][Ii][Ss][Ii][Oo][Nn]|"
                            r"\b([A-Z]{2,5})\s+(?:DIVISION|[Dd]ivision)\b|"
                            r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+[Dd]ivision\b")


def links(page_html, base):
    # Some zone sites quote attributes with ' rather than ".
    for _, href, text in re.findall(r"""<a[^>]+href=(["'])([^"'#]+)\1[^>]*>(.*?)</a>""", page_html, re.S | re.I):
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())
        yield urllib.parse.urljoin(base, html.unescape(href.strip())), text


def page_heading(page_html):
    """The page's breadcrumb, e.g. "About Us > Departments > S & T > NCR Mini diagram >
    Jhansi division". The CMS draws it as the bold links of its "SECTION LINK TABLE"
    (siblings follow in a separate sub-links table); it says what a page is better than
    the path the crawler happened to take to reach it."""
    block = re.search(r"(?is)SECTION LINK TABLE\s*-*\s*-->(.*?)SECTION LINK TABLE ENDS", page_html)
    if block:
        names = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())
                 for s in re.findall(r"(?is)<strong>(.*?)</strong>", block.group(1))]
        if names:
            return " > ".join(n for n in names if n)[:200]
    text = html.unescape(re.sub(r"<[^>]+>", "|", re.sub(r"(?is)<(script|style).*?</\1>", "", page_html)))
    cells = [" ".join(c.split()) for c in text.split("|")]
    cells = [c for c in cells if c]
    homes = [i for i, c in enumerate(cells) if c == "Home"]
    if not homes:
        return ""
    crumb = []
    for c in cells[homes[-1] + 1:homes[-1] + 8]:
        if len(c) > 120 or re.match(r"(?i)(last|updated|reviewed|source|\d)", c):
            break
        crumb.append(c)
    return " > ".join(crumb)[:200]


def classify(text, filename, trail, zone, breadcrumb=False):
    """The link and file name first; else the page. A breadcrumb lists only the page's
    ancestors, so any step of it counts; a crawl path also picks up unrelated menus, so
    there only the page's own name counts, or for a per-division sub-page ("KUR DIVISION")
    the section above it. On South Western Railway "SWR" is the zone's own name, so
    there only the spelled-out phrase counts."""
    def types(table):
        return [(n, r"station\s*working\s*rules?" if zone == "SWR" and n == "Station Working Rules" else p)
                for n, p in table]
    for name, pattern in types(DOC_TYPES):
        if re.search(pattern, f"{text} {filename}", re.I):
            return name
    steps = trail.split(" > ")
    context = steps if breadcrumb else \
        steps[-1:] + (steps[-2:-1] if re.search(r"(?i)division", steps[-1]) else [])
    for step in reversed(context):
        for name, pattern in types(PAGE_TYPES):
            if re.search(pattern, step, re.I):
                return name
    return None


def division_of(text, trail, name):
    """Nearest mention wins: the link itself, then the trail from its last step backwards."""
    for t in [text, *reversed(trail.split(" > ")), name]:
        m = DIVISION_WORDS.search(t or "")
        if m:
            return next(g for g in m.groups() if g)
    return ""


def year_of(*texts):
    years = re.findall(r"(20[0-3]\d)", " ".join(texts))
    return max(years) if years else ""


def page_key(url):
    """Drop known display options, retaining functional, blank and repeated parameters.

    Normalize language to English, while keeping page/category/etc. distinct.
    """
    parts = urllib.parse.urlsplit(url)
    display = {"fontcolor", "fontsize", "backgroundcolor", "theme"}
    keep = [(key, "0" if key.lower() == "lang" else value)
            for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in display]
    if any(key == "id" for key, _ in keep) and not any(key.lower() == "lang" for key, _ in keep):
        keep.append(("lang", "0"))
    # Stable key sorting preserves the order of repeated values for the same key.
    keep.sort(key=lambda item: item[0])
    return urllib.parse.urlunsplit(("https", parts.netloc.removeprefix("www."), parts.path,
                                    urllib.parse.urlencode(keep), ""))


def document_name(url):
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query)
    if query.get("filename"):
        return posixpath.basename(query["filename"][0])
    # URL paths use POSIX rules, even on Windows, where // can mean a UNC root.
    return urllib.parse.unquote(posixpath.basename(parts.path))


def station_document_links(page_html, base):
    """ECoR's station tables: code, name, diagram, SWR, then cable plans.

    A page can list all three kinds with identical 'Download' link text. Use
    the table column instead of inheriting 'SWR' from the page's breadcrumb.
    """
    out = {}
    for row in re.findall(r"<tr\b.*?</tr>", page_html, re.S | re.I):
        cells = re.findall(r"<td\b.*?</td>", row, re.S | re.I)
        text = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).split()) for c in cells]
        if len(cells) < 4 or not re.fullmatch(r"[A-Z]{2,5}", text[0]):
            continue
        for i, cell in enumerate(cells[2:], start=2):
            kind = "Station Diagram" if i == 2 else "Station Working Rules" if i == 3 else None
            for url, _ in links(cell, base):
                out[url] = (kind, text[0], text[1])
    return out


def crawl(zone, sub):
    print(f"[{zone}]", flush=True)
    host = f"{sub}.indianrailways.gov.in"
    # Start from both home pages: some default to Hindi menus, and the English one
    # does not always link the same sections.
    starts = [f"https://{host}/index.jsp?lang=0", f"https://{host}/"]
    seen, found = {page_key(u) for u in starts}, {}
    strong, menus = deque((u, 0, "home") for u in starts), deque()
    pages, attempts, errors = 0, 0, []
    while (strong or menus) and attempts < MAX_PAGES:
        url, depth, via = (strong or menus).popleft()
        attempts += 1
        try:
            body, ctype = fetch(url, retries=2, timeout=60)
        except Exception as error:  # keep going with the rest of the zone
            errors.append(f"{url}: {error}")
            print(f"    {zone}: skip {url[:90]}: {str(error)[:60]}", flush=True)
            continue
        if "html" not in (ctype or "html").lower():
            errors.append(f"{url}: expected HTML, received {ctype}")
            continue
        pages += 1
        page_html = body.decode("utf-8", "replace")
        heading = page_heading(page_html)
        station_links = station_document_links(page_html, url) if zone == "ECoR" else {}
        if os.environ.get("TRACE"):
            print(f"    d{depth} {url[-40:]:<42} {via[-60:]!r}")
        for link, text in links(page_html, url):
            station_info = station_links.get(link)
            if link.startswith("http://"):
                link = "https://" + link[len("http://"):]  # the servers refuse plain http
            parsed = urllib.parse.urlsplit(link)
            if not (parsed.hostname or "").endswith(".indianrailways.gov.in"):
                continue
            if FILE.search(parsed.path):
                # The sites' certificates don't cover www.<zone>...; the plain host serves the same file.
                link = link.replace("://www.", "://", 1)
                filename = document_name(link)
                # The page's breadcrumb says what it is; the crawl path is the fallback.
                context = heading or via
                kind = station_info[0] if station_info else \
                    None if NOT_A_DOCUMENT.search(f"{text} {filename}") else \
                    classify(text, filename, context, zone, breadcrumb=bool(heading))
                # Station-wise SWR tables list the code and a bare "Download"; the page says what they are.
                if kind and link not in found:
                    name = text if text.lower() not in ("download", "click here", "view", "") else filename
                    if station_info:
                        name = f"{station_info[1]} {station_info[2]}: {filename}"
                    found[link] = {
                        "zone": zone, "division": division_of(text, context, name), "document": kind,
                        "url": link, "year": year_of(text, name), "found_on": url,
                        "page": heading or " > ".join(via.split(" > ")[-2:])[:150], "link_text": name[:150],
                    }
            elif depth < MAX_DEPTH and parsed.netloc.removeprefix("www.") == host and \
                    page_key(link) not in seen and text:
                trail = text if via == "home" else f"{via} > {text}"
                if STRONG.search(text) or (STRONG.search(via) and "division" in text.lower()):
                    seen.add(page_key(link))
                    strong.append((page_key(link), depth + 1, trail))
                elif FOLLOW.search(text):
                    seen.add(page_key(link))
                    menus.append((page_key(link), depth + 1, trail))
        if pages % 10 == 0:
            print(f"  {zone}: {pages} pages read, {len(found)} documents, "
                  f"{len(strong) + len(menus)} pages queued", flush=True)
        time.sleep(PAUSE)
    result = CrawlResult(list(found.values()), pages, errors, len(strong) + len(menus))
    print(f"  {zone}: {pages} pages read, {len(found)} documents ({result.status})", flush=True)
    return result


def check_links(rows):
    """Fetch the first 1 KB of every WTT and at-a-glance link and of a few SWR/diagram links
    per zone and division: enough to see the link is alive and what it is."""
    import ssl
    import urllib.request
    from fetch_rail_data import OPENER, USER_AGENT, quote_url
    sampled = {}
    checked = 0
    for r in rows:
        key = (r["zone"], r["division"], r["document"])
        bulk = r["document"] in ("Station Working Rules", "Station Diagram")
        if bulk and sampled.get(key, 0) >= 3:
            r["link_check"] = "not checked (sampled 3 per zone/division)"
            continue
        sampled[key] = sampled.get(key, 0) + 1
        try:
            req = urllib.request.Request(quote_url(r["url"]), headers={"User-Agent": USER_AGENT, "Range": "bytes=0-1023"})
            with OPENER.open(req, timeout=60) as resp:
                head = resp.read(1024)
                kind = "PDF" if head.startswith(b"%PDF") else "zip/docx" if head.startswith(b"PK") else \
                    "Office document" if head.startswith(bytes.fromhex("D0CF11E0A1B11AE1")) else ""
                extension = os.path.splitext(urllib.parse.urlsplit(r["url"]).path)[1].lower()
                size = resp.headers.get("Content-Range", "").split("/")[-1]
                if not kind or (extension == ".pdf" and kind != "PDF"):
                    r["link_check"] = "failed: unexpected content " + resp.headers.get("Content-Type", "unknown")
                else:
                    r["link_check"] = f"ok {kind}" + (f", {int(size) / 1e6:.1f} MB" if size.isdigit() else "")
        except (urllib.error.URLError, ssl.SSLError, OSError) as error:
            r["link_check"] = f"failed: {str(error)[:60]}"
        checked += 1
        if r["link_check"].startswith("failed:"):
            print(f"  {r['zone']}: {r['link_check']} ({r['url']})", flush=True)
        if checked % 10 == 0:
            print(f"  checked {checked} document links", flush=True)
        time.sleep(PAUSE)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("zones", nargs="*", help="zone codes, e.g. ECoR NCR")
    parser.add_argument("--check", action="store_true", help="check document links in the existing CSV")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=1,
                        help="number of zones to crawl concurrently (default: 1)")
    args = parser.parse_args()
    zones = list(dict.fromkeys(args.zones or ZONES))
    unknown = [z for z in zones if z not in ZONES]
    if unknown:
        parser.error("unknown zones: " + ", ".join(unknown))
    if args.check:
        with open(OUT, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        selected = [r for r in rows if r["zone"] in zones]
        for r in selected:
            r["url"] = r["url"].replace("://www.", "://", 1)
        check_links(selected)
        write(rows)
        print(Counter(r["link_check"].split(",")[0].split(":")[0] for r in selected).most_common())
        return 1 if any(r["link_check"].startswith("failed:") for r in selected) else 0

    # Keep existing zones until their replacement has been crawled successfully.
    rows = read_rows(OUT)
    statuses = {r["zone"]: r for r in read_rows(STATUS_OUT)}
    ok = True
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(crawl, zone, ZONES[zone]): zone for zone in zones}
        for future in as_completed(futures):
            zone = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = CrawlResult([], 0, [str(error)], 0)
            previous = {r["url"]: r for r in rows if r["zone"] == zone}
            replacement = {} if result.complete else dict(previous)
            for row in result.rows:
                replacement[row["url"]] = {**previous.get(row["url"], {}), **row}
            rows = [r for r in rows if r["zone"] != zone] + list(replacement.values())
            rows.sort(key=lambda r: (r["zone"], r["division"], r["document"], r["url"]))
            statuses[zone] = {
                "zone": zone, "status": result.status, "pages_read": result.pages_read,
                "failed_pages": len(result.errors), "pending_pages": result.pending_pages,
                "documents_found": len(result.rows), "documents_saved": len(replacement),
                "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "errors": " | ".join(result.errors[:3]),
            }
            # Checkpoint after each zone, including failures, so an interruption is recoverable.
            write(rows)
            write_status(statuses)
            ok &= result.complete
            if not result.complete:
                print(f"  {zone}: incomplete crawl; keeping earlier documents not reached this time", flush=True)
    print(f"wrote {OUT}: {len(rows)} documents")
    return 0 if ok else 1


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_status(statuses):
    fields = ["zone", "status", "pages_read", "failed_pages", "pending_pages", "documents_found",
              "documents_saved", "finished_at_utc", "errors"]
    write_csv(STATUS_OUT, fields, [statuses[z] for z in sorted(statuses)])


def write(rows):
    write_csv(OUT, ["zone", "division", "document", "url", "year", "found_on",
                    "page", "link_text", "link_check"], rows)


def write_csv(path, fields, rows):
    """Write to a temporary file first, so a failure can't leave a truncated CSV."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields,
                                restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main())
