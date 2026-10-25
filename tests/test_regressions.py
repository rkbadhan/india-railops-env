"""Offline regression checks for extraction and crawler data-loss failures."""

import contextlib
import csv
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import fetch_rail_data as downloads
import find_more_sources as sources
from extract import sambalpur_freight_loads as freight
from extract import sambalpur_wtt as wtt


def source_row(zone="ECoR", url="https://example.invalid/SWR.pdf"):
    return {"zone": zone, "division": "SBP", "document": "Station Working Rules",
            "url": url, "year": "", "found_on": "listing", "page": "SBP DIVISION",
            "link_text": "SWR", "link_check": "ok PDF"}


class TimetableTests(unittest.TestCase):
    def test_first_time_keeps_adjacent_times_separate(self):
        cases = {"19.00 19.20": "19:00", "1 6 . 00": "16:00", "0 5 . 0 5": "05:05",
                 "1 4 . 0 0": "14:00", "0 9 . 30": "09:30", ";11.12": "11:12",
                 "13.35 Pre20858": "13:35", "Pre-13351 02.58": "02:58",
                 "03.20 (Power Reversal)": "03:20", "-- 11.51": "11:51",
                 "0530 06.40": "05:30", "05:30:15": "05:30:15",
                 "Pre 18407": "", "--": "", "25.99": ""}
        for cell, expected in cases.items():
            with self.subTest(cell=cell):
                self.assertEqual(wtt.first_time(cell), expected)


class DownloadTests(unittest.TestCase):
    def test_query_filenames_are_quoted_without_double_encoding(self):
        url = "https://example.invalid/download.jsp?filename=4 SWR ATS%20ENG.pdf&lang=0"
        self.assertEqual(downloads.quote_url(url),
                         "https://example.invalid/download.jsp?filename=4%20SWR%20ATS%20ENG.pdf&lang=0")

    def test_json_must_be_complete_and_structured(self):
        cases = [(b'{"elements": [', "not_json"), (b'null', "not_json"),
                 (b' \n {"valid": []}', "ok"), (b'[]', "ok"), (b'\xff', "not_json")]
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual(downloads.check_kind(body, "data.json"), expected)

    def test_overpass_runtime_error_is_not_cached_as_success(self):
        body = json.dumps({"elements": [{"type": "node"}],
                           "remark": "runtime error: Query timed out"}).encode()
        self.assertEqual(downloads.check_kind(body, "sambalpur_rail_osm.json", expected="osm"), "overpass_error")
        self.assertEqual(downloads.check_kind(b'{}', "sambalpur_rail_osm.json", expected="osm"), "not_osm_json")
        self.assertEqual(downloads.check_kind(b'{"elements": []}', "sambalpur_rail_osm.json", expected="osm"), "ok")

    def test_invalid_cached_json_is_downloaded_again(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(downloads, "OUT_DIR", tmp):
            path = os.path.join(tmp, "infrastructure", "sambalpur_rail_osm.json")
            downloads.save(b'{"elements": [', path)
            with patch.object(downloads, "fetch", return_value=(b'{"elements": []}', "application/json")) as fetch:
                with contextlib.redirect_stdout(io.StringIO()):
                    row, requested = downloads.download("infrastructure", "sambalpur_rail_osm.json", "url", "note")
            self.assertTrue(requested)
            self.assertEqual(row[3], "ok")
            fetch.assert_called_once()


class CrawlerTests(unittest.TestCase):
    def test_station_table_columns_distinguish_diagrams_rules_and_cable_plans(self):
        page = b'''<!-- THIS IS THE SECTION LINK TABLE - -->
            <strong>Station Working Rules</strong><strong>SBP DIVISION</strong>
            <!-- SECTION LINK TABLE ENDS HERE -->
            <table><tr><td>AMB</td><td>AMBODALA</td>
            <td><a href="/uploads/files/AMB.pdf">Download</a></td>
            <td><a href="//eastcoastrail.indianrailways.gov.in//downloadfile_2.jsp?filename=AMB%20ENG.pdf">Download</a></td>
            <td><a href="/uploads/files/CRP.pdf">Download</a></td></tr></table>'''
        with patch.object(sources, "fetch", return_value=(page, "text/html")), \
                patch.object(sources.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            result = sources.crawl("ECoR", "eastcoastrail")
        self.assertEqual(len(result.rows), 2)
        kinds = {sources.document_name(r["url"]): r["document"] for r in result.rows}
        self.assertEqual(kinds, {"AMB.pdf": "Station Diagram", "AMB ENG.pdf": "Station Working Rules"})
        self.assertTrue(all(r["division"] == "SBP" for r in result.rows))

    def test_document_name_handles_query_filenames_and_double_slash_paths(self):
        self.assertEqual(sources.document_name("https://example.invalid//downloadfile_2.jsp?filename=4%20SWR.pdf"),
                         "4 SWR.pdf")
        self.assertEqual(sources.document_name("https://example.invalid//WTT.pdf"), "WTT.pdf")

    def test_breadcrumb_excludes_sibling_divisions(self):
        for own, sibling in (("SBP", "KUR"), ("KUR", "SBP")):
            page = f'''<!-- THIS IS THE SECTION LINK TABLE - -->
                <strong>For IR Personnel</strong><strong>Station Working Rules</strong>
                <strong>SWR OF {own} DIVISION</strong>
                <!-- SECTION LINK TABLE ENDS HERE -->
                <!-- THIS IS THE SECTION SUB LINKS TABLE -->
                <strong>SWR OF {sibling} DIVISION</strong>'''
            with self.subTest(own=own):
                heading = sources.page_heading(page)
                self.assertEqual(sources.division_of("", heading, ""), own)
                self.assertNotIn(sibling, heading)
                self.assertEqual(sources.classify("Download", "station.pdf", heading, "ECoR", True),
                                 "Station Working Rules")

    def test_swr_zone_abbreviation_does_not_classify_unrelated_files(self):
        self.assertIsNone(sources.classify("SWR circular", "staff.pdf", "home", "SWR"))
        self.assertEqual(sources.classify("Station Working Rules", "staff.pdf", "home", "SWR"),
                         "Station Working Rules")

    def test_document_endpoints_keep_their_query_and_are_not_crawled_as_pages(self):
        page = b'''<a href="/downloadfile_2.jsp?filename=working-time-table.pdf">Working Time Table</a>
                   <a href="/root-WTT.pdf">Working Time Table</a>
                   <a href="/downloadfile_2.jsp?filename=manual.pdf">Download Manual</a>'''
        with patch.object(sources, "fetch", return_value=(page, "text/html")) as fetch, \
                patch.object(sources.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            result = sources.crawl("CR", "cr")
        self.assertTrue(result.complete)
        self.assertEqual(fetch.call_count, 2)  # Only the two home pages are HTML navigation.
        self.assertEqual(len(result.rows), 2)
        self.assertIn("https://cr.indianrailways.gov.in/downloadfile_2.jsp?filename=working-time-table.pdf",
                      [r["url"] for r in result.rows])

    def run_crawl(self, initial, results=None, argv=None, fetch_error=False):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            output = os.path.join(tmp, "nested", "sources.csv")
            status = os.path.join(tmp, "nested", "status.csv")
            stack.enter_context(patch.object(sources, "OUT", output))
            stack.enter_context(patch.object(sources, "STATUS_OUT", status))
            stack.enter_context(patch.object(sources.sys, "argv", argv or ["find_more_sources.py", "ECoR"]))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            if results is not None:
                stack.enter_context(patch.object(sources, "crawl", side_effect=lambda z, sub: results[z]))
            if fetch_error:
                stack.enter_context(patch.object(sources, "fetch", side_effect=urllib.error.URLError("offline")))
            sources.write(initial)
            code = sources.main()
            return code, sources.read_rows(output), sources.read_rows(status)

    def test_failed_zone_retains_earlier_rows_and_returns_failure(self):
        original = [source_row(), source_row("NCR")]
        code, rows, statuses = self.run_crawl(original, fetch_error=True)
        self.assertEqual(code, 1)
        self.assertCountEqual(rows, original)
        self.assertEqual(statuses[0]["status"], "unavailable")
        self.assertEqual(statuses[0]["failed_pages"], "2")

    def test_full_run_also_retains_zones_that_failed(self):
        original = [source_row("NCR")]
        results = {z: sources.CrawlResult([], 1, [], 0) for z in sources.ZONES}
        results["NCR"] = sources.CrawlResult([], 0, ["offline"], 0)
        code, rows, statuses = self.run_crawl(original, results, ["find_more_sources.py", "--workers", "4"])
        self.assertEqual(code, 1)
        self.assertEqual(rows, original)
        self.assertEqual(len(statuses), len(sources.ZONES))

    def test_partial_zone_merges_results_and_preserves_unvisited_rows(self):
        old = source_row()
        new = source_row(url="https://example.invalid/new.pdf")
        result = sources.CrawlResult([new], 2, ["listing failed"], 0)
        code, rows, statuses = self.run_crawl([old], {"ECoR": result})
        self.assertEqual(code, 1)
        self.assertCountEqual(rows, [old, new])
        self.assertEqual(statuses[0]["status"], "partial")

    def test_successful_zone_replaces_old_rows_and_keeps_other_zones(self):
        ncr = source_row("NCR")
        new = source_row(url="https://example.invalid/new.pdf")
        code, rows, statuses = self.run_crawl([source_row(), ncr],
                                             {"ECoR": sources.CrawlResult([new], 2, [], 0)})
        self.assertEqual(code, 0)
        self.assertCountEqual(rows, [new, ncr])
        self.assertEqual(statuses[0]["status"], "complete")

    def test_crawl_limit_is_reported_as_incomplete(self):
        code, rows, statuses = self.run_crawl([source_row()],
                                             {"ECoR": sources.CrawlResult([], 120, [], 5)})
        self.assertEqual(code, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(statuses[0]["status"], "page_limit")

    def test_successful_empty_crawl_records_no_documents_found(self):
        code, rows, statuses = self.run_crawl([source_row()],
                                             {"ECoR": sources.CrawlResult([], 2, [], 0)})
        self.assertEqual(code, 0)
        self.assertEqual(rows, [])
        self.assertEqual(statuses[0]["status"], "no_documents_found")

    def test_html_error_page_is_not_a_valid_pdf(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'<html>File not found</html>'
        response.headers = {"Content-Type": "text/html"}
        with patch.object(downloads.OPENER, "open", return_value=response), patch.object(sources.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()):
            row = sources.check_links([source_row()])[0]
        self.assertTrue(row["link_check"].startswith("failed:"))

    def test_link_check_failure_is_reported_in_exit_code(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'<html>Error</html>'
        response.headers = {"Content-Type": "text/html"}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(sources, "OUT", os.path.join(tmp, "sources.csv")), \
                patch.object(sources.sys, "argv", ["find_more_sources.py", "--check"]), \
                patch.object(downloads.OPENER, "open", return_value=response), \
                patch.object(sources.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            sources.write([source_row()])
            self.assertEqual(sources.main(), 1)
            self.assertTrue(sources.read_rows(sources.OUT)[0]["link_check"].startswith("failed:"))


class FreightTests(unittest.TestCase):
    def test_fresh_output_directory_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "processed", "freight_loads.csv")
            with patch.object(freight, "OUT", output), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(freight.main(), 0)
            with open(output, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 168)
            self.assertTrue(all(float(r["max_load_tonnes"]) > 0 for r in rows))


if __name__ == "__main__":
    unittest.main()
