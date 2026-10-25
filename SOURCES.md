# Sources

Every processed table in `rail_data/processed/` was extracted from the public documents below. The
documents themselves are **not** in this repository: `fetch_rail_data.py` and `fetch_sbp_station_docs.py`
download them, and `rail_data/manifest.csv` records each download's URL, size and SHA-256. The tables hold
extracted facts (running times, loop lengths, train timings), not the documents. The original publishers'
terms apply to the documents. This project is not affiliated with Indian Railways.

## What each processed table comes from

| Table | Built by | From |
| --- | --- | --- |
| `sections.csv`, `block_sections.csv`, `stations.csv`, `passenger_paths.csv`, `train_totals.csv` | `extract/sambalpur_wtt.py` | Sambalpur division Working Time Table 2023 |
| `freight_loads.csv`, `home_signals.csv` | `extract/sambalpur_freight_loads.py`, `extract/loops.py` | Sambalpur division Working Time Table 2026 (scanned; some values transcribed by hand) |
| `loops.csv` | `extract/loops.py` | Station Working Rules and station diagrams (below) |
| `hdn_sections.csv` | `extract/hdn.py` | Railway Board High Density Network report, December 2024 |
| `osm_stations.csv` | `extract/osm_check.py` | An OpenStreetMap extract plus the station documents; a consistency check, not an inventory |

Freight trains and disruptions in the benchmark problems are synthetic. See the README.

## Primary sources

| File | What it is | URL |
| --- | --- | --- |
| `WTT_ECoR_Sambalpur_2023.pdf` | Working Time Table, Sambalpur division: block sections, running times, booked paths | <https://eastcoastrail.indianrailways.gov.in/uploads/files/1697628048596-Time%20Table.pdf> |
| `WTT_ECoR_Sambalpur_2026.pdf` | Working Time Table, Sambalpur division, wef 01.01.2026 (about 90 MB) | <https://eastcoastrail.indianrailways.gov.in/uploads/files/1768821366372-Railway%20book%20Sambalpur.pdf> |
| `HDN_line_capacity_Dec2024.pdf` | 237 busiest sections: length, line type, capacity, trains per day, utilisation | <https://rb.indianrailways.gov.in/uploads/directorate/planning/High%20Density%20Network%20in%20Indian%20Railways.pdf> |

## OpenStreetMap

| File | What it is | URL |
| --- | --- | --- |
| `sambalpur_rail_osm.json` | OSM tracks, stations, switches, sidings | <https://overpass-api.de/api/interpreter> |

(c) OpenStreetMap contributors, ODbL 1.0. See `NOTICE`.

## Station Working Rules and diagrams, Sambalpur division

96 documents (68 station diagrams, 28 station working rules), each with its URL, size and checksum, in
[`rail_data/infrastructure/station_diagrams/sbp/manifest.csv`](rail_data/infrastructure/station_diagrams/sbp/manifest.csv).
Two downloads returned HTTP 404 when fetched; their rows say so.

## Other station diagrams, used as extra examples in `loops.csv`

| File | What it is | URL |
| --- | --- | --- |
| `ECoR_SWR_Jharmunda.pdf` | Station Working Rules diagram | <https://eastcoastrail.indianrailways.gov.in/uploads/files/1740642050728-SWR%20of%20JHARMUNDA.pdf> |
| `ECoR_SWR_WMY_AB_cabin.pdf` | Station Working Rules diagram | <https://eastcoastrail.indianrailways.gov.in/uploads/files/1719289727420-WMY_A_B_CABIN06062024.pdf> |
| `ECR_SWR_79_NRN.pdf` | Station Working Rules diagram, Samastipur division | <https://ecr.indianrailways.gov.in/cris//uploads/files/1704450811303-79.%20NRN.pdf> |
| `ECR_SWR_78_ARG.pdf` | Station Working Rules diagram | <https://ecr.indianrailways.gov.in/cris//uploads/files/1704450787490-78.%20ARG.pdf> |
| `ECR_SWR_84_DSKG.pdf` | Station Working Rules diagram | <https://ecr.indianrailways.gov.in/cris//uploads/files/1696920094497-84.%20DSKG.pdf> |
| `ECR_SWR_92_SBV.pdf` | Station Working Rules diagram | <https://ecr.indianrailways.gov.in/cris//uploads/files/1704451083585-92.%20SBV.pdf> |
| `ECR_SWR_31A_VKNR.pdf` | Station Working Rules diagram | <https://ecr.indianrailways.gov.in/cris//uploads/files/1696919892207-31A.%20VKNR%20PH-I%20pdf.pdf> |
| `ECR_SWR_26_HIR.pdf` | Station Working Rules diagram | <https://ecr.indianrailways.gov.in/cris//uploads/files/1724743004842-26.HIR%20FINAL%20PH.pdf> |
| `ECR_SWR_8_PPA.pdf` | Station Working Rules diagram | <https://ecr.indianrailways.gov.in/cris//uploads/files/1739186157828-8.PPA%20PH-1%20DL.pdf> |

## Downloaded for reference only

The download script also fetches the items below. No processed table is built from them, and none is redistributed.

| File | What it is | URL |
| --- | --- | --- |
| `NWR_section_at_a_glance.pdf` | North Western Railway section summary | <https://nwr.indianrailways.gov.in/uploads/files/1742970384051-6-section%20at%20a%20glance.pdf> |
| `stations.json` | Station coordinates, zone, state (CC0, ~2016) | <https://raw.githubusercontent.com/datameet/railways/master/stations.json> |
| `trains.json` | Train routes (CC0, ~2016) | <https://raw.githubusercontent.com/datameet/railways/master/trains.json> |
| `schedules.json` | Stop-by-stop schedules (CC0, ~2016) | <https://raw.githubusercontent.com/datameet/railways/master/schedules.json> |
| `CR_goods_sheds.pdf` | Where freight loads and unloads, Central Railway | <https://cr.indianrailways.gov.in/cris//uploads/files/1739513488573-List%20of%20Goods%20Sheds.pdf> |
| `CR_goods_sheds_2.pdf` | Goods sheds, Central Railway (second list) | <https://cr.indianrailways.gov.in/cris//uploads/files/1747739651236-goods%20shed.pdf> |
| `CR_goods_sidings.pdf` | Private sidings, Central Railway | <https://cr.indianrailways.gov.in/cris//uploads/files/1747829073806-Goods-Siding.pdf> |
| `RB_TMS_FOIS_premises_classification_2024.pdf` | Railway Board classification of freight premises | <https://rb.indianrailways.gov.in/uploads/directorate/traffic_comm/Rates-Letters/2024/System%20(TMS%20FOIS)%20based%20classification%20of%20Railway%20premises%20%20(goods%20shed%2C%20station%20etc_)%20dated%2023_07_2024.pdf> |
| `RB_freight_stock_targets_2024-25.pdf` | Wagon turnaround and freight stock targets | <https://rb.indianrailways.gov.in/uploads/directorate/mec_engg/downloads/freight/2024/Freight%20Stock%20Performance%20Target%20for%202024-25.pdf> |
| `ECR_freight_statistics.pdf` | East Central Railway freight statistics | <https://ecr.indianrailways.gov.in/uploads/files/1730199735136-Freight%20Statistics.pdf> |
| `RS_PQ_Feb2024.pdf` | Rajya Sabha unstarred Q908 (09.02.2024): average speed of passenger and freight trains | <https://sansad.in/getFile/annex/263/AU908.pdf?source=pqars> |
| `ISB_thesis_freight_traffic_management_2026.pdf` | How section controllers hold and release freight (real IR data) | <https://eprints.exchange.isb.edu/id/eprint/2458/1/Essays%20on%20Freight%20Traffic%20Management%20in%20Indian%20Railways.pdf> |
| `SER_GSR_2011.pdf` | General & Subsidiary Rules, South Eastern Railway | <https://ser.indianrailways.gov.in/cris/uploads/files/1606473656907-G&SR%202011%20Full.pdf> |
| `SCR_GSR.pdf` | General & Subsidiary Rules, South Central Railway | <https://scr.indianrailways.gov.in/cris//uploads/files/1421322916862-G%20&%20SR%20Upto%20AS14.pdf> |
| `SECR_Operating_Manual_2025.pdf` | Operating manual: train precedence, control procedures | <https://secr.indianrailways.gov.in/uploads/files/1760593351226-Operating%20Manual%202025.pdf> |
| `RB_SR_chapter5_station_working.pdf` | Control and working of stations | <https://rb.indianrailways.gov.in/uploads/directorate/safety/SR_SR/SR_SR_CHAP5.PDF> |
| `DISPLIB_2025.pdf` | DISPLIB: common format and benchmark for train dispatching | <https://arxiv.org/pdf/2509.12254> |
| `RSTGCN_Indian_delays_2025.pdf` | Nationwide Indian train delay dataset paper | <https://arxiv.org/pdf/2510.01262> |
| `Indian_delays_zero_shot_markov_2018.pdf` | 135 Indian trains, two years of running status | <https://arxiv.org/pdf/1806.02825> |
| `IITB_zero_based_timetable_preprint.pdf` | IIT Bombay + IR zero-based timetabling | <https://easychair.org/publications/preprint/4FgM/open> |
| `RL_single_track_rescheduling_Leeds_2024.pdf` | Q-learning train rescheduling, single track | <https://eprints.whiterose.ac.uk/id/eprint/217651/1/1-s2.0-S2210970624000532-main.pdf> |
| `displib_spec_2025-09-17.pdf` | DISPLIB problem and file format specification | <https://displib.github.io/displib_spec_2025-09-17.pdf> |
| `displib_problems_2025-09-17.zip` | DISPLIB: all problem instances (JSON) | <https://displib.github.io/displib_problems_2025-09-17.zip> |
| `displib_solutions_2025-09-17.zip` | DISPLIB: best known solutions (JSON) | <https://displib.github.io/displib_solutions_2025-09-17.zip> |
| `displib_instances_testing.zip` | DISPLIB: small test instances | <https://displib.github.io/displib_instances_testing.zip> |
| `displib_verify_v0_3.zip` | DISPLIB solution verification program (Python) | <https://displib.github.io/displib_verify_v0_3.zip> |
| `displib_index.html` | DISPLIB home page, with the table of instance sizes and best known objectives | <https://displib.github.io/> |

DataMeet's railways collection is labelled CC0 by DataMeet. Research papers and DISPLIB files are reference
material under their own terms.
