"""Fetch missing cover art for audiobooks.

Given a title (and optionally an author), query free public catalogues and
return a downloaded image file. No API keys, stdlib only (urllib + json).

Source order (first hit wins):
  1. iTunes Search API  (audiobook media — best matches for audiobooks)
  2. Open Library       (covers.openlibrary.org)
  3. Google Books       (volumeInfo.imageLinks)

If nothing is found and Pillow OR ffmpeg is available, a simple text cover
can be generated as a last resort (see generate_placeholder).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from typing import Optional

_UA = "chapterize/0.1 (+https://github.com/cwfrazier1/chapterize)"
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def _get_json(url: str, timeout: float) -> Optional[dict]:
    try:
        return json.loads(_get(url, timeout).decode("utf-8", "replace"))
    except Exception:
        return None


def _looks_like_image(data: bytes) -> Optional[str]:
    if data[:3] == _JPEG_MAGIC:
        return ".jpg"
    if data[:8] == _PNG_MAGIC:
        return ".png"
    return None


def _download_image(url: str, dest_dir: str, timeout: float) -> Optional[str]:
    try:
        data = _get(url, timeout)
    except Exception:
        return None
    ext = _looks_like_image(data)
    if not ext or len(data) < 2048:  # reject 1x1 trackers / html
        return None
    fd, path = tempfile.mkstemp(prefix="cover_", suffix=ext, dir=dest_dir)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


# ---------------------------------------------------------------------------
# Individual catalogue sources
# ---------------------------------------------------------------------------
def _src_itunes(title: str, author: str, dest_dir: str, timeout: float) -> Optional[str]:
    term = " ".join(t for t in (title, author) if t)
    q = urllib.parse.urlencode({"term": term, "media": "audiobook", "limit": 5})
    data = _get_json(f"https://itunes.apple.com/search?{q}", timeout)
    if not data:
        # fall back to ebook/music-video artwork if audiobook has no hit
        q = urllib.parse.urlencode({"term": term, "entity": "ebook", "limit": 5})
        data = _get_json(f"https://itunes.apple.com/search?{q}", timeout)
    for item in (data or {}).get("results", []):
        art = item.get("artworkUrl100") or item.get("artworkUrl60")
        if art:
            # request a large version instead of the 100px thumbnail
            big = re.sub(r"/\d+x\d+bb\.(jpg|png)", r"/1200x1200bb.\1", art)
            got = _download_image(big, dest_dir, timeout) or \
                _download_image(art, dest_dir, timeout)
            if got:
                return got
    return None


def _src_openlibrary(title: str, author: str, dest_dir: str, timeout: float) -> Optional[str]:
    params = {"title": title, "limit": 5}
    if author:
        params["author"] = author
    q = urllib.parse.urlencode(params)
    data = _get_json(f"https://openlibrary.org/search.json?{q}", timeout)
    for doc in (data or {}).get("docs", []):
        cid = doc.get("cover_i")
        if cid:
            url = f"https://covers.openlibrary.org/b/id/{cid}-L.jpg"
            got = _download_image(url, dest_dir, timeout)
            if got:
                return got
    return None


def _src_googlebooks(title: str, author: str, dest_dir: str, timeout: float) -> Optional[str]:
    q = f'intitle:{title}'
    if author:
        q += f'+inauthor:{author}'
    url = "https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode(
        {"q": q, "maxResults": 5})
    data = _get_json(url, timeout)
    for item in (data or {}).get("items", []):
        links = (item.get("volumeInfo") or {}).get("imageLinks") or {}
        for key in ("extraLarge", "large", "medium", "thumbnail", "smallThumbnail"):
            if links.get(key):
                # bump zoom + drop edge curl for a cleaner image
                url = links[key].replace("&edge=curl", "").replace("zoom=1", "zoom=3")
                url = url.replace("http://", "https://")
                got = _download_image(url, dest_dir, timeout)
                if got:
                    return got
    return None


_SOURCES = {
    "itunes": _src_itunes,
    "openlibrary": _src_openlibrary,
    "googlebooks": _src_googlebooks,
}


def find_cover(title: str,
               author: str = "",
               dest_dir: Optional[str] = None,
               *,
               sources=("itunes", "openlibrary", "googlebooks"),
               timeout: float = 15.0) -> Optional[str]:
    """Return a path to a downloaded cover image, or None if nothing found.

    Caller owns the returned temp file and should delete it when done.
    """
    title = (title or "").strip()
    if not title:
        return None
    dest_dir = dest_dir or tempfile.gettempdir()
    for name in sources:
        fn = _SOURCES.get(name)
        if not fn:
            continue
        try:
            path = fn(title, author.strip(), dest_dir, timeout)
        except Exception:
            path = None
        if path:
            return path
    return None


# ---------------------------------------------------------------------------
# Generated placeholder (last resort)
# ---------------------------------------------------------------------------
def _wrap(text: str, width: int) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines[:6])


def _mac_or_linux_font() -> Optional[str]:
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ):
        if os.path.isfile(p):
            return p
    return None


def generate_placeholder(title: str, author: str, dest_dir: Optional[str] = None,
                         *, ffmpeg: Optional[str] = None,
                         size: int = 1000) -> Optional[str]:
    """Render a simple text cover with ffmpeg's drawtext. Best-effort."""
    exe = ffmpeg or shutil.which("ffmpeg")
    font = _mac_or_linux_font()
    if not exe or not font:
        return None
    dest_dir = dest_dir or tempfile.gettempdir()
    fd, path = tempfile.mkstemp(prefix="cover_gen_", suffix=".png", dir=dest_dir)
    os.close(fd)

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\u2019")

    title_t = esc(_wrap(title.upper(), 18))
    author_t = esc(author.strip())
    vf = (
        f"drawtext=fontfile='{font}':text='{title_t}':fontcolor=white:"
        f"fontsize=64:x=(w-text_w)/2:y=h*0.30:line_spacing=14:text_align=C"
    )
    if author_t:
        vf += (
            f",drawtext=fontfile='{font}':text='{author_t}':fontcolor=0xB9C4D6:"
            f"fontsize=40:x=(w-text_w)/2:y=h*0.72"
        )
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"color=c=0x1E293B:s={size}x{size}",
           "-vf", vf, "-frames:v", "1", path]
    try:
        subprocess.run(cmd, check=True, timeout=30)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        return None
    return path
