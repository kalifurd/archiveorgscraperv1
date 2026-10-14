#!/usr/bin/env python3
"""Build an offline library of public-domain books and art.

Sources:
  books  archive.org texts (public domain by date or by license only)
  art    Art Institute of Chicago or the Met (public-domain works only)

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


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
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
                 language=None, year_from=None, year_to=None):
    exclude = " OR ".join(sorted(LENDING_COLLECTIONS))
    q = f"({query}) AND mediatype:texts AND NOT collection:({exclude})"
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
              language=None, year_from=None, year_to=None):
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
                "source": "archive.org",
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


# --- saving --------------------------------------------------------------

def save(entry, out_dir, dry_run, download=http_download):
    folder = os.path.join(out_dir, entry["kind"] + "s", safe_filename(entry["id"]))
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


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("kind", choices=["books", "art"])
    p.add_argument("query", help='search terms, e.g. "botany" or "subject:astronomy"')
    p.add_argument("--source", choices=["aic", "met"], default="aic",
                   help="art source (default aic)")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--from", dest="year_from", type=int, metavar="YEAR",
                   help="earliest year (book published, or artwork made)")
    p.add_argument("--to", dest="year_to", type=int, metavar="YEAR",
                   help="latest year (book published, or artwork made)")
    p.add_argument("--language", default="eng",
                   help='book language code, e.g. eng, fre, ger; "any" for all '
                        "(default eng)")
    p.add_argument("--out", default=os.path.expanduser("~/library"))
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be saved, download nothing")
    args = p.parse_args(argv)
    language = None if args.language.lower() == "any" else args.language
    era = dict(year_from=args.year_from, year_to=args.year_to)
    if None not in era.values() and args.year_from > args.year_to:
        p.error("--from must not be later than --to")

    if args.kind == "books":
        print(f"Searching archive.org texts for: {args.query}")
        plans = (plan_book(i, language=language, **era)
                 for i in search_books(args.query, args.limit, language=language, **era))
    elif args.source == "aic":
        print(f"Searching Art Institute of Chicago for: {args.query}")
        plans = plan_art_aic(args.query, args.limit, **era)
    else:
        print(f"Searching the Met for: {args.query}")
        plans = plan_art_met(args.query, args.limit, **era)

    saved = skipped = 0
    for entry, reason in plans:
        if entry is None:
            skipped += 1
            print(f"  skip: {reason}")
            continue
        print(f"{entry['title']} ({entry['source']} {entry['id']})")
        save(entry, args.out, args.dry_run)
        saved += 1
    print(f"\n{saved} allowed, {skipped} skipped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
