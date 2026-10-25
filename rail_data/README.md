# Railway data guide

Run commands from the repository root; setup and command order are in the root
[README](../README.md). Raw downloads are not redistributed: `fetch_rail_data.py`
recreates them. The processed CSVs in `processed/` and the download manifest
`manifest.csv` are included. See [SOURCES.md](../SOURCES.md) for every source.

## Raw inputs and provenance

| Directory | Contents | How to recreate |
| --- | --- | --- |
| `infrastructure/` | Sambalpur WTTs (2023 and 2026), Railway Board HDN capacity report (December 2024), NWR section summary | `python fetch_rail_data.py --only infrastructure` |
| `infrastructure/station_diagrams/` | Station working rules and diagrams; the `sbp/` subdirectory holds Sambalpur listings | Main downloader, then `python fetch_sbp_station_docs.py` |
| `infrastructure/osm/` | Overpass rail extract for the configured Sambalpur-area bounding box | `python fetch_rail_data.py --only osm` |
| `timetables/datameet/` | Station, train, and schedule JSON from DataMeet's railway collection (circa 2016) | `python fetch_rail_data.py --only timetables` |
| `freight/`, `rules/`, `research/` | Published freight statistics, rules, and research references | Main downloader with the corresponding `--only` layer |

`manifest.csv` records `layer`, `file`, `url`, `status`, `bytes`, `sha256`, and
`what_it_is` for the curated downloads. The station-document downloader has a
separate manifest under `infrastructure/station_diagrams/sbp/`, including station
code, name, document kind, source URL, status, size, and checksum. Manifests
describe download attempts; a checksum identifies bytes, not their accuracy.

The 2023 Sambalpur WTT download contains 100 PDF pages. The 2026 WTT is scanned;
freight load values, loop speeds, and home-signal locations from it are manually
transcribed in the extractor sources. Public availability does not establish a
uniform licence: DataMeet labels its collection CC0, OSM is ODbL, and the other
publishers' terms must be consulted before redistribution.

## Processed CSVs

All CSVs are UTF-8 with a header row. Blank fields represent missing or
unextracted information; they must not be treated as zero. Page references are
one-based PDF page indexes used by the extractors.

| File | Producer | Grain and principal fields |
| --- | --- | --- |
| `sections.csv` | `extract/sambalpur_wtt.py` | One section: length in km, line type, block system, electrification, signalling, source page |
| `block_sections.csv` | Same | Directed block-section running time: section, direction, from/to station, distance km, running time minutes, speed km/h; includes printed `TOTAL` rows |
| `stations.csv` | Same | Station or IBS post per section: chainage, distance from UP origin, direction, class, signalling/interlocking, facing-point speed, printed text, page |
| `passenger_paths.csv` | Same | Train visit per section/direction: train metadata and suspended flag, station, printed chainage, arrival/departure, raw time cells, allowance codes, crossing/precedence notes, page |
| `train_totals.csv` | Same | Printed train/section totals: engineering allowance, recovery, path/diversion minutes, raw path/diversion text, page |
| `freight_loads.csv` | `extract/sambalpur_freight_loads.py` | Section/loco/weather/unit combination: traction, maximum tonnes, printed value, ruling gradient, curve degree, note, source file/page |
| `loops.csv` | `extract/loops.py` | Station running line: line number/type, printed description, clear standing length metres, length basis, loop speed, status, source and transcription method; includes explicit missing records |
| `home_signals.csv` | Same | Station signal inventory: signal count, UP/DN and third-line chainage references, remarks, source file/page |
| `hdn_sections.csv` | `extract/hdn.py` | Corridor section: railway/division/route, length, track arrangement, working system, capacity/trains each way, utilisation percent, printed values, works, page |
| `osm_stations.csv` | `extract/osm_check.py` | Station-code comparison: OSM match, sampled track counts, nearby service-track lengths in metres, SWR/diagram counts, verdict |

## Interpretation and limitations

- The inputs cover different dates and geographic scopes. The extracted
  passenger timetable is from 2023, freight/load and signal transcriptions are
  from 2026, HDN capacities describe December 2024, and the OSM snapshot has its
  own timestamp. They are not a single operational snapshot.
- Train times are clock times, without service-date or overnight rollover
  inference. `first_time` selects the first readable time when a raw cell holds
  multiple values; retain the raw fields for interpretation. Suspended train
  columns can appear in the WTT and are retained with a `suspended` flag. The
  extractor combines marks across later pages as well as the first page and
  refreshes the earlier rows. The problem generator excludes suspended services.
- Railway chainage is not geographic distance. `passenger_paths.km` retains
  each table's printed chainage convention. `stations.km_from_origin` uses the
  section's UP origin. IBS posts can differ by direction and are not station
  identifiers for joins.
- `TOTAL` rows in `block_sections.csv` are section summaries; exclude them when
  building individual blocks or summing block lengths.
- Loop lengths come from parsed SWR text or manual diagram transcription.
  Source checks verify transcribed lengths against available PDF text, without
  proving that each line label is correct. `loops.csv` also contains example
  stations outside Sambalpur; filter `division == ECoR/SBP` for that scope.
- Freight transcription preserves questionable printed values and documents
  corrections such as `S` read as `5`. SU/MU/TU mean one, two, and three units.
  Do not silently replace flagged values with inferred operating limits.
- HDN extraction carries down merged cells and retains printed numeric fields.
  Rated-section counts are checked against the report. Utilisation-band and
  route-km comparisons are printed; route-km differences are not fatal checks.
- OSM track counts sample short cross-sections near station nodes. Curves,
  junctions, unmapped tracks, track tagging, and station placement can affect the
  comparison. The verdict is a consistency hint, not an authoritative inventory.
- Crawler classifications and division/year labels are inferred from link text,
  filenames, and listing breadcrumbs. A filename timestamp can resemble a year;
  confirm dates in the document. Menu depth and page limits constrain coverage.
  A row marked `not checked` was not individually validated. A successful first
  1 KB signature check does not prove the full file is readable or correctly
  classified.
- `complete` and `no_documents_found` in `crawl_status.csv` mean the queued pages
  within the configured crawl limits were read without request errors.
  `partial`, `page_limit`, and `unavailable` mark incomplete attempts; older
  documents are retained when they were not reached. The root README explains
  how to retry selected zones and recheck their links.

## Reproduction checks

The extractor outputs before the October 23, 2026 fixes contained 8 sections,
150 block-section/total rows, 107 station rows, 4,152 passenger-path rows,
259 train totals, 168 freight loads, 360 running-line/missing rows, 70 home-signal
rows, 245 HDN rows (237 rated), and 73 OSM comparison rows. These are reference
counts for the downloaded source files, not permanent assertions about new
editions or live OSM data.

The fixes were exercised with Python 3.12.9, certifi 2026.10.19,
pdfplumber 0.11.10, and PyMuPDF 2.0. Offline regression checks run with:

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -v
```
