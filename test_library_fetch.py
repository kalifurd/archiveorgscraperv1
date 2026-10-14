"""Offline tests for library_fetch. Run: python -m unittest -v"""
import datetime
import json
import os
import tempfile
import unittest

import library_fetch as lf

TODAY = datetime.date(2026, 10, 14)  # cutoff year 1931


class BookRules(unittest.TestCase):
    def check(self, meta):
        return lf.book_allowed(meta, TODAY)

    def test_lending_library_rejected_even_if_old(self):
        ok, why = self.check({"collection": ["inlibrary", "printdisabled"], "year": "1890"})
        self.assertFalse(ok)
        self.assertIn("lending", why)

    def test_lending_collection_match_ignores_case(self):
        self.assertFalse(self.check({"collection": "InLibrary", "year": "1890"})[0])

    def test_access_restricted_rejected(self):
        self.assertFalse(self.check({"access-restricted-item": "true", "year": "1900"})[0])

    def test_old_book_allowed(self):
        self.assertTrue(self.check({"year": "1930"})[0])

    def test_cutoff_year_itself_rejected(self):
        self.assertFalse(self.check({"year": "1931"})[0])

    def test_date_field_used_when_no_year(self):
        self.assertTrue(self.check({"date": "1899-05-01"})[0])

    def test_modern_book_without_license_rejected(self):
        ok, why = self.check({"year": "1987"})
        self.assertFalse(ok)
        self.assertIn("1987", why)

    def test_no_year_no_license_rejected(self):
        self.assertFalse(self.check({"title": "Mystery"})[0])

    def test_cc_license_allowed(self):
        meta = {"year": "2015",
                "licenseurl": "http://creativecommons.org/licenses/by-sa/4.0/"}
        self.assertTrue(self.check(meta)[0])

    def test_public_domain_mark_allowed(self):
        meta = {"licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/"}
        self.assertTrue(self.check(meta)[0])

    def test_cutoff_moves_with_the_year(self):
        self.assertEqual(lf.pd_cutoff_year(datetime.date(2030, 1, 1)), 1935)


class SafeFilename(unittest.TestCase):
    def test_strips_path_traversal(self):
        self.assertEqual(lf.safe_filename("../../etc/passwd"), "passwd")
        self.assertEqual(lf.safe_filename("sub\\dir\\book.epub"), "book.epub")

    def test_never_empty(self):
        self.assertEqual(lf.safe_filename(".."), "file")


def fake_json(responses):
    def get(url):
        for key, value in responses.items():
            if key in url:
                return value
        raise AssertionError(f"unexpected url {url}")
    return get


class Sources(unittest.TestCase):
    def test_search_excludes_lending_collections(self):
        seen = []

        def get(url):
            seen.append(url)
            return {"response": {"docs": [{"identifier": "abc"}]}}
        self.assertEqual(lf.search_books("botany", 5, get), ["abc"])
        self.assertIn("inlibrary", seen[0])
        self.assertIn("mediatype%3Atexts", seen[0])

    def test_plan_book_prefers_epub(self):
        get = fake_json({"metadata/oldbook": {
            "metadata": {"year": "1880", "title": "Ferns"},
            "files": [{"name": "ferns.pdf", "format": "PDF"},
                      {"name": "ferns.epub", "format": "EPUB"}]}})
        entry, _ = lf.plan_book("oldbook", TODAY, get)
        self.assertEqual(entry["filename"], "ferns.epub")
        self.assertTrue(entry["url"].endswith("/oldbook/ferns.epub"))

    def test_plan_book_skips_lending(self):
        get = fake_json({"metadata/lent": {
            "metadata": {"collection": ["inlibrary"], "year": "1880"},
            "files": [{"name": "x.epub", "format": "EPUB"}]}})
        entry, why = lf.plan_book("lent", TODAY, get)
        self.assertIsNone(entry)
        self.assertIn("lent", why)

    def test_aic_rechecks_public_domain(self):
        get = fake_json({"artic.edu": {
            "config": {"iiif_url": "https://iiif.test"},
            "data": [{"id": 1, "is_public_domain": True, "image_id": "img1"},
                     {"id": 2, "is_public_domain": False, "image_id": "img2"},
                     {"id": 3, "is_public_domain": True, "image_id": None}]}})
        results = list(lf.plan_art_aic("lilies", 3, get))
        allowed = [e for e, _ in results if e]
        self.assertEqual([e["id"] for e in allowed], ["1"])
        self.assertTrue(allowed[0]["url"].startswith("https://iiif.test/img1/"))

    def test_met_rechecks_public_domain(self):
        get = fake_json({
            "search?": {"objectIDs": [10, 11]},
            "objects/10": {"isPublicDomain": True, "primaryImage": "https://img/10.jpg"},
            "objects/11": {"isPublicDomain": False, "primaryImage": "https://img/11.jpg"},
        })
        allowed = [e for e, _ in lf.plan_art_met("ships", 5, get) if e]
        self.assertEqual([e["id"] for e in allowed], ["10"])


class Saving(unittest.TestCase):
    entry = {"kind": "book", "source": "archive.org", "id": "oldbook",
             "title": "Ferns", "basis": "published 1880", "url": "https://x/f.epub",
             "filename": "f.epub"}

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            lf.save(self.entry, d, dry_run=True,
                    download=lambda *a: self.fail("downloaded in dry run"))
            self.assertEqual(os.listdir(d), [])

    def test_save_writes_manifest_and_skips_existing(self):
        calls = []

        def download(url, dest):
            calls.append(url)
            open(dest, "w").close()
            return "deadbeef"
        with tempfile.TemporaryDirectory() as d:
            lf.save(self.entry, d, dry_run=False, download=download)
            lf.save(self.entry, d, dry_run=False, download=download)
            self.assertEqual(len(calls), 1)
            with open(os.path.join(d, "manifest.jsonl")) as f:
                rows = [json.loads(line) for line in f]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["path"], os.path.join("books", "oldbook", "f.epub"))
            self.assertEqual(rows[0]["sha256"], "deadbeef")


if __name__ == "__main__":
    unittest.main()
