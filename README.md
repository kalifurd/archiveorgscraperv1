# Library Fetch

Builds an offline library of **public-domain books, art, audio and images** for home learning: material kids can study that isn't in the public-school curriculum. Download it once and it's available with no internet. (Wikipedia and other wiki content belongs in Kiwix, not here.)

It only keeps items whose own metadata shows they are free to use:

| Kind | `--source` | What it fetches | Allowed when |
|---|---|---|---|
| `books` | `archive` (default) | Books from archive.org (EPUB, else PDF, else text) | Published more than 95 years ago (US public domain; the cutoff moves forward each January), **or** a Creative Commons / Public Domain Mark license |
| `books` | `bhl` | Biodiversity Heritage Library natural-history books (via archive.org) | Same rule as above |
| `art` | `aic` (default) | Art Institute of Chicago images | `is_public_domain` is true (CC0) |
| `art` | `met` | The Met images | `isPublicDomain` is true (CC0) |
| `audio` | `librivox` (default) | LibriVox audiobooks (via archive.org), every track | Open license on the item, nothing else counts |
| `textbooks` | `openstax` | OpenStax textbooks (PDF) | Creative Commons license |
| `images` | `nasa` (default) | NASA images | US government work, **skipped** if its credits mention a copyright holder |
| `images` | `loc` | Library of Congress photos | Rights advisory says "no known restrictions" |

Archive.org's **lending library is always skipped**: the `inlibrary`, `printdisabled` and `lendinglibrary` collections, and any access-restricted item. Those are scanned in-copyright books meant for borrowing, not keeping.

Audio never uses the "old enough" rule: sound recordings follow different copyright terms, so only an explicit open license lets one in.

Nothing is streamed. Each file is saved once, and every saved file gets a line in `manifest.jsonl` with its source, URL, SHA-256 and the reason it was allowed.

## Requirements

- Python 3.9+
- Nothing else. It uses only the standard library.

## Usage

Always try a dry run first. It lists what would be saved and why, and downloads nothing:

```bash
python library_fetch.py books "botany" --dry-run
```

Then fetch for real:

```bash
# Books from archive.org
python library_fetch.py books "subject:astronomy" --limit 20 --out ~/library

# Art from the Art Institute of Chicago (default) or the Met
python library_fetch.py art "lilies" --limit 25 --out ~/library
python library_fetch.py art "sailing ships" --source met --out ~/library

# Natural history from the Biodiversity Heritage Library
python library_fetch.py books "ferns" --source bhl

# Audiobooks
python library_fetch.py audio "Jules Verne"

# OpenStax textbooks: words must all appear in the title; "all" lists every book
python library_fetch.py textbooks "biology"

# NASA and Library of Congress images
python library_fetch.py images "Saturn" --from 1970 --to 1990
python library_fetch.py images "covered bridge" --source loc
```

### Narrowing by era and language

```bash
# Victorian-era botany, in English (English is the default)
python library_fetch.py books "subject:botany" --from 1837 --to 1901

# Anything before 1850, any language
python library_fetch.py books "subject:astronomy" --to 1850 --language any

# French books
python library_fetch.py books "subject:botany" --language fre

# Art made between 1800 and 1899
python library_fetch.py art "landscape" --from 1800 --to 1899
```

- `--from` / `--to` match the year a book was published, an artwork was begun, a photo was taken or a recording was dated. They don't apply to textbooks. Either end can be left open. Items with no known year are skipped when an era is set.
- `--language` takes a code such as `eng`, `fre`, `ger`, `spa`, `ita`, `lat`; `any` turns the filter off. It applies to books and audiobooks.
- Book results come back most-downloaded first, so well-known titles show up before obscure ones.

Era and language are checked twice: once in the search, and again against each item's own record before saving.

The book query uses archive.org's search syntax, e.g. `subject:astronomy`, `creator:"Darwin, Charles"`, or plain words.

Running the same command again skips files you already have.

## Library layout

```
~/library/
  manifest.jsonl          one JSON line per saved file
  books/<archive-id>/     the book file
  arts/<museum-id>/       museum artwork
  audio/<archive-id>/     every track of one audiobook or album
  textbooks/<title>/      the textbook PDF
  images/<nasa-or-loc-id>/ the photo
```

## Notes

- The "published before" rule is US law. Outside the US, public-domain terms differ.
- Archive.org dates can be wrong (sometimes the scan date is recorded instead of the publication date). The manifest records the year the decision was based on, so it can be checked.
- Requests are spaced one second apart to be polite to these free services.

## Tests

```bash
python -m unittest -v
```

The tests run offline against canned responses.
