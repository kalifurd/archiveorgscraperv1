# Library Fetch

Builds an offline library of **public-domain books and art** for home learning: material kids can study that isn't in the public-school curriculum. Download it once and it's available with no internet.

It only keeps items whose own metadata shows they are free to use:

| Source | What it fetches | Allowed when |
|---|---|---|
| archive.org | Books (EPUB, else PDF, else plain text) | Published more than 95 years ago (US public domain; the cutoff moves forward each January), **or** the item carries a Creative Commons / Public Domain Mark license |
| Art Institute of Chicago | Artwork images | `is_public_domain` is true (CC0) |
| The Met | Artwork images | `isPublicDomain` is true (CC0) |

Archive.org's **lending library is always skipped**: the `inlibrary`, `printdisabled` and `lendinglibrary` collections, and any access-restricted item. Those are scanned in-copyright books meant for borrowing, not keeping.

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
```

The book query uses archive.org's search syntax, e.g. `subject:astronomy`, `creator:"Darwin, Charles"`, or plain words.

Running the same command again skips files you already have.

## Library layout

```
~/library/
  manifest.jsonl          one JSON line per saved file
  books/<archive-id>/     the book file
  arts/<museum-id>/       the image
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
