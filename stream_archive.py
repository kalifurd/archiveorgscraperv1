import os
import subprocess
from internetarchive import search_items, get_item

def stream_archive_movie(search_query="collection:scifimovies"):
    print(f"Searching Archive.org for: {search_query}...")
    
    # 1. Search for items (limiting to 10 for a quick list)
    search = search_items(search_query)
    results = []
    
    for i, result in enumerate(search):
        if i >= 10: break
        results.append(result['identifier'])
        print(f"[{i}] {result['identifier']}")

    # 2. User selects a movie
    choice = int(input("\nEnter the number of the movie you want to stream: "))
    identifier = results[choice]
    
    # 3. Get the item and find the best .mp4 file
    item = get_item(identifier)
    mp4_files = [f.name for f in item.files if f.name.endswith('.mp4')]

    if not mp4_files:
        print("No playable mp4 files found for this item.")
        return

    # Usually the largest mp4 is the best quality
    video_file = mp4_files[0]
    # Construct the direct URL
    stream_url = f"https://archive.org/download/{identifier}/{video_file}"
    
    print(f"\nStreaming: {video_file}")
    print(f"URL: {stream_url}")

    # 4. Open in VLC (Change 'vlc' to the full path if it's not in your PATH)
    # Windows example: r"C:\Program Files\VideoLAN\VLC\vlc.exe"
    try:
        subprocess.run(['vlc', stream_url])
    except FileNotFoundError:
        print("\nError: VLC was not found. Please make sure VLC is installed and in your system PATH.")

if __name__ == "__main__":
    stream_archive_movie()