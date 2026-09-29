"""Discover recent uploads from the two YouTube channels watched by Capital.

Prints JSON. This module never sends notifications or changes portfolio rules.
"""

import json
import re
import urllib.request


CHANNELS = {
    "SvyatoslavKonenkov": "Святослав Коненков",
    "TheRichestDee": "Богатейший Ди",
}
USER_AGENT = "Mozilla/5.0 (Capital video monitor)"


def recent_videos(handle, limit=10):
    url = f"https://www.youtube.com/@{handle}/videos"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        page = response.read().decode("utf-8", "replace")
    # The /videos page renders the channel's uploads in order. Each card repeats
    # videoId in thumbnails, navigation and metadata; preserve only the first.
    ids = list(dict.fromkeys(re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', page)))
    if not ids:
        raise RuntimeError(f"No videos found for @{handle}; YouTube markup may have changed")
    return [{"id": video_id, "channel": CHANNELS[handle],
             "url": f"https://www.youtube.com/watch?v={video_id}"}
            for video_id in ids[:limit]]


def main():
    print(json.dumps({handle: recent_videos(handle) for handle in CHANNELS}, ensure_ascii=False))


if __name__ == "__main__":
    main()
