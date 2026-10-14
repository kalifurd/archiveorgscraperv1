"""Offline tests for library_fetch. Run: python -m unittest -v"""
import datetime
import json
import os
import tempfile
import unittest
import urllib.parse

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


class EraAndLanguage(unittest.TestCase):
    def test_in_era(self):
        self.assertTrue(lf.in_era(1850, 1800, 1899))
        self.assertTrue(lf.in_era(1800, 1800, 1899))
        self.assertFalse(lf.in_era(1900, 1800, 1899))
        self.assertTrue(lf.in_era(1700, None, 1899))
        self.assertTrue(lf.in_era(None))
        self.assertFalse(lf.in_era(None, 1800, None))

    def test_language_matches_code_or_name(self):
        self.assertTrue(lf.language_matches({"language": "eng"}, "eng"))
        self.assertTrue(lf.language_matches({"language": ["English"]}, "eng"))
        self.assertFalse(lf.language_matches({"language": "ger"}, "eng"))
        self.assertFalse(lf.language_matches({}, "eng"))
        self.assertTrue(lf.language_matches({"language": "ger"}, None))

    def test_search_adds_language_era_and_popularity_sort(self):
        seen = []

        def get(url):
            seen.append(urllib.parse.unquote_plus(url))
            return {"response": {"docs": []}}
        lf.search_books("botany", 5, get, language="eng", year_from=1800, year_to=1899)
        self.assertIn('language:(eng OR "English")', seen[0])
        self.assertIn("year:[1800 TO 1899]", seen[0])
        self.assertIn("sort[]=downloads desc", seen[0])

    def test_open_ended_era_in_search(self):
        seen = []

        def get(url):
            seen.append(urllib.parse.unquote_plus(url))
            return {"response": {"docs": []}}
        lf.search_books("botany", 5, get, year_to=1850)
        self.assertIn("year:[* TO 1850]", seen[0])
        self.assertNotIn("language:", seen[0])

    def book(self, **meta):
        return fake_json({"metadata/b": {
            "metadata": dict(meta), "files": [{"name": "b.epub", "format": "EPUB"}]}})

    def test_plan_book_rechecks_era(self):
        entry, why = lf.plan_book("b", TODAY, self.book(year="1910"),
                                  year_from=1800, year_to=1899)
        self.assertIsNone(entry)
        self.assertIn("outside era 1800-1899", why)

    def test_plan_book_rechecks_language(self):
        entry, why = lf.plan_book("b", TODAY, self.book(year="1880", language="ger"),
                                  language="eng")
        self.assertIsNone(entry)
        self.assertIn("language", why)
        entry, _ = lf.plan_book("b", TODAY, self.book(year="1880", language="eng"),
                                language="eng")
        self.assertIsNotNone(entry)

    def test_aic_era(self):
        seen = []
        data = {"data": [
            {"id": 1, "is_public_domain": True, "image_id": "a", "date_start": 1850},
            {"id": 2, "is_public_domain": True, "image_id": "b", "date_start": 1920}]}

        def get(url):
            seen.append(urllib.parse.unquote_plus(url))
            return data
        allowed = [e for e, _ in lf.plan_art_aic("x", 5, get, year_from=1800, year_to=1899) if e]
        self.assertEqual([e["id"] for e in allowed], ["1"])
        self.assertIn("[range][date_start][gte]=1800", seen[0])

    def test_met_era_sends_both_ends(self):
        seen = []

        def get(url):
            seen.append(urllib.parse.unquote_plus(url))
            if "search?" in url:
                return {"objectIDs": [10, 11]}
            year = 1850 if url.endswith("/10") else 1750
            return {"isPublicDomain": True, "primaryImage": "https://img/x.jpg",
                    "objectBeginDate": year}
        allowed = [e for e, _ in lf.plan_art_met("x", 5, get, year_from=1800) if e]
        self.assertEqual([e["id"] for e in allowed], ["10"])
        self.assertIn("dateBegin=1800", seen[0])
        self.assertIn("dateEnd=", seen[0])


class Cli(unittest.TestCase):
    def test_from_after_to_rejected(self):
        with self.assertRaises(SystemExit):
            lf.main(["books", "x", "--from", "1900", "--to", "1800", "--dry-run"])


class NewSources(unittest.TestCase):
    def test_bhl_searches_biodiversity_collection(self):
        seen = []

        def get(url):
            seen.append(urllib.parse.unquote_plus(url))
            return {"response": {"docs": []}}
        lf.search_books("ferns", 3, get, collection=lf.ARCHIVE_COLLECTIONS["bhl"])
        self.assertIn("collection:biodiversity", seen[0])

    def test_open_license_rules(self):
        self.assertTrue(lf.open_license(
            {"licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/"})[0])
        self.assertFalse(lf.open_license({"year": "1890"})[0])
        self.assertFalse(lf.open_license({"collection": "inlibrary",
            "licenseurl": "http://creativecommons.org/publicdomain/zero/1.0/"})[0])

    def test_audio_takes_every_track_of_best_format(self):
        get = fake_json({"metadata/lv": {
            "metadata": {"title": "Walden",
                         "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/"},
            "files": [{"name": "walden_02.mp3", "format": "64Kbps MP3"},
                      {"name": "walden_01.mp3", "format": "64Kbps MP3"},
                      {"name": "walden_01.ogg", "format": "Ogg Vorbis"},
                      {"name": "cover.jpg", "format": "JPEG"}]}})
        plans = lf.plan_audio("lv", get)
        self.assertEqual([e["filename"] for e, _ in plans],
                         ["walden_01.mp3", "walden_02.mp3"])
        self.assertTrue(all(e["kind"] == "audio" for e, _ in plans))

    def test_audio_without_license_skipped_even_if_old(self):
        get = fake_json({"metadata/old": {
            "metadata": {"year": "1905"},
            "files": [{"name": "a.mp3", "format": "VBR MP3"}]}})
        [(entry, why)] = lf.plan_audio("old", get)
        self.assertIsNone(entry)
        self.assertIn("no open license", why)

    def test_openstax_filters_by_title_and_license(self):
        get = fake_json({"openstax.org": {"items": [
            {"title": "Biology 2e", "book_state": "live",
             "license_url": "https://creativecommons.org/licenses/by/4.0/",
             "license_name": "Creative Commons Attribution License",
             "low_resolution_pdf_url": "https://cdn.test/bio.pdf"},
            {"title": "Microbiology", "book_state": "live",
             "license_url": "https://example.test/custom",
             "low_resolution_pdf_url": "https://cdn.test/micro.pdf"},
            {"title": "Calculus Volume 1", "book_state": "live",
             "license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
             "high_resolution_pdf_url": "https://cdn.test/calc.pdf"}]}})
        results = list(lf.plan_textbooks_openstax("bio", 5, get))
        allowed = [e for e, _ in results if e]
        self.assertEqual([e["id"] for e in allowed], ["biology-2e"])
        self.assertEqual(len(results), 2)  # Microbiology matched "bio" but has no CC license
        everything = [e for e, _ in lf.plan_textbooks_openstax("all", 5, get) if e]
        self.assertEqual(len(everything), 2)
        self.assertEqual(everything[1]["url"], "https://cdn.test/calc.pdf")

    def test_nasa_skips_third_party_and_picks_large(self):
        def get(url):
            if "/search?" in url:
                return {"collection": {"items": [
                    {"data": [{"nasa_id": "A1", "title": "Saturn",
                               "date_created": "1981-08-25T00:00:00Z"}]},
                    {"data": [{"nasa_id": "B2", "title": "Comet",
                               "description": "Image copyright J. Smith",
                               "date_created": "2005-01-01T00:00:00Z"}]}]}}
            return {"collection": {"items": [
                {"href": "http://images-assets.nasa.gov/image/A1/A1~orig.jpg"},
                {"href": "http://images-assets.nasa.gov/image/A1/A1~large.jpg"}]}}
        results = list(lf.plan_images_nasa("saturn", 5, get))
        allowed = [e for e, _ in results if e]
        self.assertEqual([e["id"] for e in allowed], ["A1"])
        self.assertEqual(allowed[0]["url"],
                         "https://images-assets.nasa.gov/image/A1/A1~large.jpg")
        self.assertIn("copyright", results[1][1])

    def test_loc_requires_no_known_restrictions(self):
        def get(url):
            if "/photos/?" in url:
                return {"results": [
                    {"id": "https://www.loc.gov/item/111/", "title": "Barn",
                     "date": "1890", "image_url": ["https://tile.loc.gov/s.jpg#h=1",
                                                   "https://tile.loc.gov/l.jpg#h=9"]},
                    {"id": "http://www.loc.gov/item/222/", "title": "Ad",
                     "date": "1950", "image_url": ["https://tile.loc.gov/x.jpg"]}]}
            if "/111/" in url:
                return {"item": {"rights_advisory": ["No known restrictions on publication."],
                                 "date": "1890"}}
            return {"item": {"rights_advisory": "Rights status not evaluated."}}
        results = list(lf.plan_images_loc("barn", 5, get))
        allowed = [e for e, _ in results if e]
        self.assertEqual([e["id"] for e in allowed], ["111"])
        self.assertEqual(allowed[0]["url"], "https://tile.loc.gov/l.jpg")
        self.assertIn("rights not clear", results[1][1])

    def test_kind_folders(self):
        calls = []
        with tempfile.TemporaryDirectory() as d:
            entry = {"kind": "audio", "source": "x", "id": "lv", "title": "t",
                     "basis": "b", "url": "u", "filename": "a.mp3"}
            lf.save(entry, d, dry_run=False,
                    download=lambda url, dest: calls.append(dest) or open(dest, "w").close() or "h")
            self.assertTrue(calls[0].endswith(os.path.join("audio", "lv", "a.mp3")))

    def test_cli_rejects_wrong_source_for_kind(self):
        with self.assertRaises(SystemExit):
            lf.main(["books", "x", "--source", "nasa", "--dry-run"])


if __name__ == "__main__":
    unittest.main()
