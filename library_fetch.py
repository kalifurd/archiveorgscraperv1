#!/usr/bin/env python3
"""Build an offline library of public-domain books, art, audio and images.

Sources:
  books      archive.org texts, or the Biodiversity Heritage Library on
             archive.org (public domain by date or by license only)
  art        Art Institute of Chicago or the Met (public-domain works only)
  audio      LibriVox audiobooks on archive.org
             (open license only)
  textbooks  OpenStax (Creative Commons textbooks)
  images     NASA or the Library of Congress (US government works, or
             "no known restrictions")

Every item is checked against its own metadata before anything is saved.
Archive.org's lending library (scanned in-copyright books) is always skipped.
Each saved file gets a line in <out>/manifest.jsonl recording where it came
from and why it was allowed.

Stdlib only. Run with --dry-run first to see what would be fetched.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "library-fetch/1.0 (offline homeschool library)"
REQUEST_DELAY_S = 1.0

# US rule: works published more than 95 years ago are public domain.
PD_TERM_YEARS = 95

# Archive.org collections that hold borrow-only, in-copyright scans.
LENDING_COLLECTIONS = {"inlibrary", "printdisabled", "lendinglibrary"}

PD_LICENSE_PATTERNS = (
    "creativecommons.org/publicdomain/",  # CC0 and Public Domain Mark
    "creativecommons.org/licenses/",      # any CC license
)

# archive.org records language as a code or a name, so search for both.
LANGUAGE_NAMES = {
    "eng": "English", "fre": "French", "ger": "German", "spa": "Spanish",
    "ita": "Italian", "lat": "Latin", "por": "Portuguese", "dut": "Dutch",
}

# Best format first.
BOOK_FORMATS = ("EPUB", "Text PDF", "PDF", "DjVuTXT")
AUDIO_FORMATS = ("VBR MP3", "64Kbps MP3", "128Kbps MP3", "Ogg Vorbis", "Flac")

# archive.org collections behind the named sources.
ARCHIVE_COLLECTIONS = {"bhl": "biodiversity", "librivox": "librivoxaudio"}

# How many files of one item a dry run lists (an audiobook can have 50 tracks).
DRY_RUN_FILES_SHOWN = 2

# Library folder for each kind of item.
KIND_FOLDERS = {"book": "books", "art": "arts", "audio": "audio",
                "textbook": "textbooks", "image": "images"}

# Words in an image's own credits that mean someone else holds rights.
THIRD_PARTY_RIGHTS = re.compile(r"©|copyright|all rights reserved", re.I)


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read(300).decode("utf-8", "replace").strip()
        raise SystemExit(f"HTTP {e.code} from {url}\n{body}") from None
    time.sleep(REQUEST_DELAY_S)
    return data


def http_download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = dest + ".part"
    sha = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=300) as resp, open(tmp, "wb") as out:
        for chunk in iter(lambda: resp.read(1 << 16), b""):
            sha.update(chunk)
            out.write(chunk)
    os.replace(tmp, dest)
    time.sleep(REQUEST_DELAY_S)
    return sha.hexdigest()


# --- license rules -------------------------------------------------------

def pd_cutoff_year(today=None):
    """Works published before this year are US public domain."""
    today = today or datetime.date.today()
    return today.year - PD_TERM_YEARS


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _pub_year(meta):
    for key in ("year", "date"):
        for value in _as_list(meta.get(key)):
            m = re.match(r"\s*(\d{4})", str(value))
            if m:
                return int(m.group(1))
    return None


def book_allowed(meta, today=None):
    """Return (allowed, reason) for one archive.org item's metadata."""
    collections = {c.lower() for c in _as_list(meta.get("collection"))}
    lending = collections & LENDING_COLLECTIONS
    if lending:
        return False, f"lending library ({', '.join(sorted(lending))})"
    if str(meta.get("access-restricted-item", "")).lower() == "true":
        return False, "access restricted"

    license_url = str(meta.get("licenseurl") or "").lower()
    if any(p in license_url for p in PD_LICENSE_PATTERNS):
        return True, f"license {meta['licenseurl']}"

    year = _pub_year(meta)
    cutoff = pd_cutoff_year(today)
    if year is not None and year < cutoff:
        return True, f"published {year}, before {cutoff}"
    if year is None:
        return False, "no license and no publication year"
    return False, f"published {year}, not before {cutoff}, no open license"


def open_license(meta):
    """Return (allowed, reason) when an item must carry an open license."""
    collections = {c.lower() for c in _as_list(meta.get("collection"))}
    if collections & LENDING_COLLECTIONS:
        return False, "lending library"
    if str(meta.get("access-restricted-item", "")).lower() == "true":
        return False, "access restricted"
    license_url = str(meta.get("licenseurl") or "").lower()
    if any(p in license_url for p in PD_LICENSE_PATTERNS):
        return True, f"license {meta['licenseurl']}"
    return False, "no open license"


def in_era(year, year_from=None, year_to=None):
    """True when year falls inside the era. Unknown years only pass an open era."""
    if year_from is None and year_to is None:
        return True
    if year is None:
        return False
    return (year_from is None or year >= year_from) and (year_to is None or year <= year_to)


def era_label(year_from, year_to):
    return f"{year_from if year_from is not None else '…'}-{year_to if year_to is not None else '…'}"


def language_matches(meta, language):
    """True when the item is in the language, or the language filter is off."""
    if not language:
        return True
    wanted = {language.lower(), LANGUAGE_NAMES.get(language.lower(), language).lower()}
    have = {str(v).strip().lower() for v in _as_list(meta.get("language"))}
    return bool(have & wanted)


def safe_filename(name):
    """Flatten a remote file name so it can't escape the target folder."""
    base = os.path.basename(name.replace("\\", "/"))
    base = re.sub(r"[^\w.\- ]", "_", base).strip(". ")
    return base or "file"


# --- sources -------------------------------------------------------------

def search_books(query, limit, get_json=http_get_json, *,
                 language=None, year_from=None, year_to=None,
                 mediatype="texts", collection=None):
    exclude = " OR ".join(sorted(LENDING_COLLECTIONS))
    q = f"({query}) AND mediatype:{mediatype} AND NOT collection:({exclude})"
    if collection:
        q += f" AND collection:{collection}"
    if language:
        name = LANGUAGE_NAMES.get(language.lower(), language)
        q += f' AND language:({language} OR "{name}")'
    if year_from is not None or year_to is not None:
        lo = year_from if year_from is not None else "*"
        hi = year_to if year_to is not None else "*"
        q += f" AND year:[{lo} TO {hi}]"
    params = urllib.parse.urlencode({
        "q": q, "fl[]": "identifier", "sort[]": "downloads desc",
        "rows": limit, "output": "json"})
    data = get_json(f"https://archive.org/advancedsearch.php?{params}")
    return [d["identifier"] for d in data["response"]["docs"]]


def plan_book(identifier, today=None, get_json=http_get_json, *,
              language=None, year_from=None, year_to=None, source="archive.org"):
    data = get_json(f"https://archive.org/metadata/{urllib.parse.quote(identifier)}")
    meta = data.get("metadata", {})
    allowed, reason = book_allowed(meta, today)
    if not allowed:
        return None, f"{identifier}: {reason}"
    if not in_era(_pub_year(meta), year_from, year_to):
        return None, f"{identifier}: outside era {era_label(year_from, year_to)}"
    if not language_matches(meta, language):
        return None, f"{identifier}: not in language {language}"
    files = {f.get("format"): f["name"] for f in data.get("files", [])}
    for fmt in BOOK_FORMATS:
        if fmt in files:
            name = files[fmt]
            return {
                "kind": "book",
                "source": source,
                "id": identifier,
                "title": meta.get("title"),
                "creator": meta.get("creator"),
                "basis": reason,
                "url": "https://archive.org/download/"
                       f"{urllib.parse.quote(identifier)}/{urllib.parse.quote(name)}",
                "filename": safe_filename(name),
            }, reason
    return None, f"{identifier}: no EPUB/PDF/text file"


def plan_art_aic(query, limit, get_json=http_get_json, *, year_from=None, year_to=None):
    params = {
        "q": query,
        "query[bool][must][0][term][is_public_domain]": "true",
        "fields": "id,title,artist_display,date_display,date_start,image_id,"
                  "is_public_domain",
        "limit": limit,
    }
    if year_from is not None:
        params["query[bool][must][1][range][date_start][gte]"] = year_from
    if year_to is not None:
        params["query[bool][must][2][range][date_start][lte]"] = year_to
    params = urllib.parse.urlencode(params)
    data = get_json(f"https://api.artic.edu/api/v1/artworks/search?{params}")
    iiif = data.get("config", {}).get("iiif_url", "https://www.artic.edu/iiif/2")
    for art in data.get("data", []):
        if art.get("is_public_domain") is not True:
            yield None, f"aic {art.get('id')}: not public domain"
            continue
        if not art.get("image_id"):
            yield None, f"aic {art.get('id')}: no image"
            continue
        if not in_era(art.get("date_start"), year_from, year_to):
            yield None, f"aic {art.get('id')}: outside era {era_label(year_from, year_to)}"
            continue
        yield {
            "kind": "art",
            "source": "artic.edu",
            "id": str(art["id"]),
            "title": art.get("title"),
            "creator": art.get("artist_display"),
            "date": art.get("date_display"),
            "basis": "is_public_domain (CC0)",
            "url": f"{iiif}/{art['image_id']}/full/843,/0/default.jpg",
            "filename": f"aic-{art['id']}.jpg",
        }, "ok"


def plan_art_met(query, limit, get_json=http_get_json, *, year_from=None, year_to=None):
    base = "https://collectionapi.metmuseum.org/public/collection/v1"
    params = {"q": query, "isPublicDomain": "true", "hasImages": "true"}
    if year_from is not None or year_to is not None:
        # The Met needs both ends of a date range.
        params["dateBegin"] = year_from if year_from is not None else -5000
        params["dateEnd"] = year_to if year_to is not None else datetime.date.today().year
    params = urllib.parse.urlencode(params)
    ids = (get_json(f"{base}/search?{params}").get("objectIDs") or [])[:limit]
    for object_id in ids:
        obj = get_json(f"{base}/objects/{object_id}")
        if obj.get("isPublicDomain") is not True:
            yield None, f"met {object_id}: not public domain"
            continue
        image = obj.get("primaryImage")
        if not image:
            yield None, f"met {object_id}: no image"
            continue
        if not in_era(obj.get("objectBeginDate"), year_from, year_to):
            yield None, f"met {object_id}: outside era {era_label(year_from, year_to)}"
            continue
        yield {
            "kind": "art",
            "source": "metmuseum.org",
            "id": str(object_id),
            "title": obj.get("title"),
            "creator": obj.get("artistDisplayName"),
            "date": obj.get("objectDate"),
            "basis": "isPublicDomain (CC0)",
            "url": image,
            "filename": f"met-{object_id}" + (os.path.splitext(image)[1] or ".jpg"),
        }, "ok"


def plan_audio(identifier, get_json=http_get_json, *, language=None, year_from=None,
               year_to=None, source="archive.org"):
    """Every track of one archive.org audio item, in the best format it has."""
    data = get_json(f"https://archive.org/metadata/{urllib.parse.quote(identifier)}")
    meta = data.get("metadata", {})
    allowed, reason = open_license(meta)
    if not allowed:
        return [(None, f"{identifier}: {reason}")]
    if not language_matches(meta, language):
        return [(None, f"{identifier}: not in {language}")]
    if not in_era(_pub_year(meta), year_from, year_to):
        return [(None, f"{identifier}: outside era {era_label(year_from, year_to)}")]
    files = data.get("files", [])
    for fmt in AUDIO_FORMATS:
        tracks = sorted(f["name"] for f in files if f.get("format") == fmt)
        if tracks:
            return [({
                "kind": "audio",
                "source": source,
                "id": identifier,
                "title": meta.get("title"),
                "creator": meta.get("creator"),
                "basis": reason,
                "url": "https://archive.org/download/"
                       f"{urllib.parse.quote(identifier)}/{urllib.parse.quote(name)}",
                "filename": safe_filename(name),
            }, reason) for name in tracks]
    return [(None, f"{identifier}: no MP3/Ogg/FLAC files")]


def plan_textbooks_openstax(query, limit, get_json=http_get_json):
    fields = ("title,book_state,license_name,license_url,"
              "high_resolution_pdf_url,low_resolution_pdf_url")
    books, page = [], 20
    while True:
        data = get_json("https://openstax.org/apps/cms/api/v2/pages/?type=books.Book"
                        f"&fields={fields}&limit={page}&offset={len(books)}")
        items = data.get("items", [])
        books += items
        total = data.get("meta", {}).get("total_count", 0)
        if len(items) < page or len(books) >= total:
            break
    words = [w for w in query.lower().split() if w not in ("*", "all")]
    found = 0
    for book in books:
        title = book.get("title") or ""
        if words and not all(w in title.lower() for w in words):
            continue
        if found >= limit:
            break
        found += 1
        license_url = str(book.get("license_url") or "").lower()
        if "creativecommons.org/licenses/" not in license_url:
            yield None, f"openstax {title}: no Creative Commons license"
            continue
        if book.get("book_state") not in (None, "live"):
            yield None, f"openstax {title}: {book.get('book_state')}"
            continue
        pdf = book.get("low_resolution_pdf_url") or book.get("high_resolution_pdf_url")
        if not pdf:
            yield None, f"openstax {title}: no PDF"
            continue
        slug = safe_filename(re.sub(r"\s+", "-", title.lower()))
        yield {
            "kind": "textbook",
            "source": "openstax.org",
            "id": slug,
            "title": title,
            "basis": f"license {book.get('license_name') or book['license_url']}",
            "url": pdf,
            "filename": f"{slug}.pdf",
        }, "ok"


def plan_images_nasa(query, limit, get_json=http_get_json, *, year_from=None, year_to=None):
    params = {"q": query, "media_type": "image", "page_size": limit}
    if year_from is not None:
        params["year_start"] = year_from
    if year_to is not None:
        params["year_end"] = year_to
    data = get_json("https://images-api.nasa.gov/search?" + urllib.parse.urlencode(params))
    for item in data.get("collection", {}).get("items", [])[:limit]:
        info = (item.get("data") or [{}])[0]
        nasa_id = info.get("nasa_id")
        if not nasa_id:
            continue
        credits = " ".join(str(info.get(k, "")) for k in
                           ("description", "photographer", "secondary_creator"))
        if THIRD_PARTY_RIGHTS.search(credits):
            yield None, f"nasa {nasa_id}: credits name a copyright holder"
            continue
        if not in_era(_pub_year({"date": info.get("date_created")}), year_from, year_to):
            yield None, f"nasa {nasa_id}: outside era {era_label(year_from, year_to)}"
            continue
        asset = get_json(f"https://images-api.nasa.gov/asset/{urllib.parse.quote(nasa_id)}")
        hrefs = [i.get("href", "") for i in asset.get("collection", {}).get("items", [])]
        url = next((h for size in ("~large.jpg", "~medium.jpg", "~orig.jpg")
                    for h in hrefs if h.endswith(size)), None)
        if not url:
            yield None, f"nasa {nasa_id}: no JPEG"
            continue
        yield {
            "kind": "image",
            "source": "nasa.gov",
            "id": nasa_id,
            "title": info.get("title"),
            "creator": info.get("photographer") or info.get("center"),
            "date": info.get("date_created"),
            "basis": "NASA media (US government work)",
            "url": url.replace("http://", "https://", 1),
            "filename": safe_filename(url.rsplit("/", 1)[-1]),
        }, "ok"


def plan_images_loc(query, limit, get_json=http_get_json, *, year_from=None, year_to=None):
    params = {"q": query, "fo": "json", "c": limit}
    if year_from is not None or year_to is not None:
        lo = year_from if year_from is not None else 1000
        hi = year_to if year_to is not None else datetime.date.today().year
        params["dates"] = f"{lo}/{hi}"
    data = get_json("https://www.loc.gov/photos/?" + urllib.parse.urlencode(params))
    for result in data.get("results", [])[:limit]:
        item_url = re.sub(r"^http://", "https://", result.get("id") or result.get("url") or "")
        if not item_url.startswith("https://www.loc.gov/"):
            continue
        detail = get_json(item_url.rstrip("/") + "/?fo=json").get("item", {})
        rights = " ".join(str(r) for r in _as_list(detail.get("rights_advisory")))
        item_id = item_url.rstrip("/").rsplit("/", 1)[-1]
        if "no known restrictions" not in rights.lower():
            yield None, f"loc {item_id}: rights not clear ({rights[:60] or 'none stated'})"
            continue
        if not in_era(_pub_year({"date": detail.get("date") or result.get("date")}),
                      year_from, year_to):
            yield None, f"loc {item_id}: outside era {era_label(year_from, year_to)}"
            continue
        images = [u.split("#", 1)[0] for u in _as_list(result.get("image_url"))]
        if not images:
            yield None, f"loc {item_id}: no image"
            continue
        yield {
            "kind": "image",
            "source": "loc.gov",
            "id": item_id,
            "title": result.get("title"),
            "date": result.get("date"),
            "basis": f"rights advisory: {rights.strip()}",
            "url": images[-1],
            "filename": f"loc-{safe_filename(item_id)}"
                        + (os.path.splitext(images[-1])[1] or ".jpg"),
        }, "ok"


# --- saving --------------------------------------------------------------

def save(entry, out_dir, dry_run, download=http_download):
    folder = os.path.join(out_dir, KIND_FOLDERS[entry["kind"]], safe_filename(entry["id"]))
    dest = os.path.join(folder, entry["filename"])
    if dry_run:
        print(f"  would save {dest}  [{entry['basis']}]")
        return
    if os.path.exists(dest):
        print(f"  already have {dest}")
        return
    os.makedirs(folder, exist_ok=True)
    entry = dict(entry, path=os.path.relpath(dest, out_dir),
                 sha256=download(entry["url"], dest),
                 fetched=datetime.date.today().isoformat())
    with open(os.path.join(out_dir, "manifest.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"  saved {dest}")


SOURCES = {
    "books": ["archive", "bhl"],
    "art": ["aic", "met"],
    "audio": ["librivox"],
    "textbooks": ["openstax"],
    "images": ["nasa", "loc"],
}


def make_plans(kind, source, query, limit, language, era):
    if kind == "books":
        label = "archive.org (BHL)" if source == "bhl" else "archive.org"
        ids = search_books(query, limit, language=language,
                           collection=ARCHIVE_COLLECTIONS.get(source), **era)
        return (plan_book(i, language=language, source=label, **era) for i in ids)
    if kind == "audio":
        ids = search_books(query, limit, mediatype="audio", language=language,
                           collection=ARCHIVE_COLLECTIONS[source], **era)
        label = f"archive.org ({source})"
        return (plan for i in ids
                for plan in plan_audio(i, language=language, source=label, **era))
    if kind == "textbooks":
        return plan_textbooks_openstax(query, limit)
    return {
        "aic": plan_art_aic, "met": plan_art_met,
        "nasa": plan_images_nasa, "loc": plan_images_loc,
    }[source](query, limit, **era)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("kind", choices=list(SOURCES))
    p.add_argument("query", help='search terms, e.g. "botany" or "subject:astronomy"')
    p.add_argument("--source", help="where to fetch from; default is the first listed: "
                   + "; ".join(f"{k}: {', '.join(v)}" for k, v in SOURCES.items()))
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--from", dest="year_from", type=int, metavar="YEAR",
                   help="earliest year (book published, or artwork made)")
    p.add_argument("--to", dest="year_to", type=int, metavar="YEAR",
                   help="latest year (book published, or artwork made)")
    p.add_argument("--language", default="eng",
                   help='book or audiobook language code, e.g. eng, fre, ger; '
                        '"any" for all (default eng)')
    p.add_argument("--out", default=os.path.expanduser("~/library"))
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be saved, download nothing")
    args = p.parse_args(argv)
    language = None if args.language.lower() == "any" else args.language
    era = dict(year_from=args.year_from, year_to=args.year_to)
    if None not in era.values() and args.year_from > args.year_to:
        p.error("--from must not be later than --to")
    source = args.source or SOURCES[args.kind][0]
    if source not in SOURCES[args.kind]:
        p.error(f"{args.kind} sources are: {', '.join(SOURCES[args.kind])}")

    print(f"Searching {source} for: {args.query}")
    plans = make_plans(args.kind, source, args.query, args.limit, language, era)

    items = files = skipped = hidden = 0
    last_id = None
    for entry, reason in plans:
        if entry is None:
            skipped += 1
            print(f"  skip: {reason}")
            continue
        if entry["id"] != last_id:
            last_id, shown = entry["id"], 0
            items += 1
            print(f"{entry['title']} ({entry['source']} {entry['id']})")
        files += 1
        if args.dry_run and shown >= DRY_RUN_FILES_SHOWN:
            hidden += 1
            continue
        shown += 1
        save(entry, args.out, args.dry_run)
    note = f" ({files} files)" if files != items else ""
    print(f"\n{items} allowed{note}, {skipped} skipped.")
    if hidden:
        print("Dry run lists the first few files of each item; the rest are counted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
