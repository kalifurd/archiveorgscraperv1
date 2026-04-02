# Archive.org Scraper v1

A Python tool to search and stream movies from Archive.org's collection.

## Features

- Search Archive.org for items by collection or query
- Display available results
- Stream selected movies directly to VLC player
- Support for mp4 format files

## Requirements

- Python 3.7+
- VLC media player (for streaming)
- pip (Python package manager)

## Installation

1. Clone the repository:
```bash
git clone https://github.com/kalifurd/archiveorgscraperv1.git
cd archiveorgscraperv1
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Make sure VLC is installed and accessible from your system PATH

## Usage

Run the script:
```bash
python stream_archive.py
```

The script will:
1. Search Archive.org for science fiction movies (default collection)
2. Display the first 10 results with numbers
3. Prompt you to select a movie by entering its number
4. Stream the selected movie in VLC

## Customization

To search a different collection, modify the search query in `stream_archive.py`:

```python
stream_archive_movie(search_query="your_custom_query")
```

Common Archive.org collections:
- `collection:scifimovies` - Science fiction movies
- `collection:classicfilms` - Classic films
- `mediatype:movies` - All movies

## Dependencies

- **internetarchive** - Archive.org API client
- **requests** - HTTP library
- **BeautifulSoup4** - HTML/XML parsing

## License 

MIT

## Notes

- Large files may take time to start streaming
- Ensure stable internet connection for smooth playback
- VLC must be installed on your system