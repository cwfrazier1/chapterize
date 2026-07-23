"""Core conversion engine for chapterize.

Wraps ffmpeg/ffprobe. No third-party Python dependencies — stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional


class ChapterizeError(RuntimeError):
    """Raised for user-facing failures (missing ffmpeg, bad input, etc.)."""


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe discovery
# ---------------------------------------------------------------------------
def _find(binary: str, override: Optional[str] = None) -> str:
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override
        raise ChapterizeError(f"{binary} not found at {override!r}")
    found = shutil.which(binary)
    if not found:
        raise ChapterizeError(
            f"'{binary}' is not installed or not on PATH.\n"
            "  macOS:  brew install ffmpeg\n"
            "  Debian: sudo apt install ffmpeg\n"
            "  Fedora: sudo dnf install ffmpeg"
        )
    return found


def ffmpeg_bin(override: Optional[str] = None) -> str:
    return _find("ffmpeg", override)


def ffprobe_bin(override: Optional[str] = None) -> str:
    return _find("ffprobe", override)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Chapter:
    start: float  # seconds
    title: str
    end: Optional[float] = None  # seconds; filled in later


@dataclass
class ProbeResult:
    duration: float
    codec: str
    channels: int
    sample_rate: int
    bit_rate: Optional[int]
    tags: dict = field(default_factory=dict)
    chapters: list = field(default_factory=list)
    has_cover: bool = False


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------
def probe(path: str, ffprobe: Optional[str] = None) -> ProbeResult:
    if not os.path.isfile(path):
        raise ChapterizeError(f"Input file not found: {path}")
    exe = ffprobe_bin(ffprobe)
    cmd = [
        exe, "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", "-show_chapters", path,
    ]
    try:
        out = subprocess.check_output(cmd, text=True)
    except subprocess.CalledProcessError as e:  # pragma: no cover
        raise ChapterizeError(f"ffprobe failed on {path}: {e}") from e
    data = json.loads(out)

    fmt = data.get("format", {})
    astream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "audio"),
        {},
    )
    has_cover = any(
        s.get("codec_type") == "video"
        or s.get("disposition", {}).get("attached_pic")
        for s in data.get("streams", [])
    )

    duration = float(fmt.get("duration") or astream.get("duration") or 0.0)
    chapters = []
    for ch in data.get("chapters", []):
        try:
            start = float(ch.get("start_time"))
            end = float(ch.get("end_time"))
        except (TypeError, ValueError):
            continue
        title = (ch.get("tags") or {}).get("title") or f"Chapter {len(chapters)+1}"
        chapters.append(Chapter(start=start, title=title, end=end))

    return ProbeResult(
        duration=duration,
        codec=astream.get("codec_name", "?"),
        channels=int(astream.get("channels") or 0),
        sample_rate=int(astream.get("sample_rate") or 0),
        bit_rate=int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
        tags=fmt.get("tags", {}) or {},
        chapters=chapters,
        has_cover=has_cover,
    )


def read_embedded_chapters(path: str, ffprobe: Optional[str] = None) -> list:
    return probe(path, ffprobe).chapters


# ---------------------------------------------------------------------------
# Chapter generation strategies
# ---------------------------------------------------------------------------
def equal_chapters(duration: float, *, count: Optional[int] = None,
                   length: Optional[float] = None,
                   name_fmt: str = "Chapter {n}") -> list:
    """Split into `count` equal parts, or fixed `length`-second parts."""
    if duration <= 0:
        raise ChapterizeError("Cannot split: unknown media duration.")
    if length:
        n = max(1, int((duration + length - 1) // length))
        step = length
    elif count:
        n = max(1, int(count))
        step = duration / n
    else:
        raise ChapterizeError("equal_chapters needs either count or length.")
    chapters = []
    for i in range(n):
        start = i * step
        if start >= duration:
            break
        chapters.append(Chapter(start=start, title=name_fmt.format(n=i + 1)))
    return chapters


_SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)")
_SILENCE_START = re.compile(r"silence_start:\s*([-0-9.]+)")


def detect_silence_chapters(path: str, *, noise_db: float = -30.0,
                            min_silence: float = 0.8,
                            min_chapter: float = 90.0,
                            name_fmt: str = "Chapter {n}",
                            ffmpeg: Optional[str] = None,
                            duration: Optional[float] = None,
                            progress: Optional[Callable[[str], None]] = None) -> list:
    """Detect chapter boundaries at long silences.

    A boundary is placed at the *end* of each qualifying silence. Boundaries
    closer than `min_chapter` seconds to the previous one are merged away so a
    noisy recording doesn't shatter into hundreds of chapters.
    """
    exe = ffmpeg_bin(ffmpeg)
    cmd = [
        exe, "-hide_banner", "-nostats", "-i", path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
        "-f", "null", "-",
    ]
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            text=True)
    ends: list = []
    assert proc.stderr is not None
    for line in proc.stderr:
        m = _SILENCE_END.search(line)
        if m:
            ends.append(float(m.group(1)))
            if progress:
                progress(f"silence boundary @ {float(m.group(1)):.0f}s")
    proc.wait()

    # Always start at 0, then add silence ends that are far enough apart.
    boundaries = [0.0]
    for t in ends:
        if t - boundaries[-1] >= min_chapter:
            boundaries.append(t)
    chapters = [Chapter(start=b, title=name_fmt.format(n=i + 1))
                for i, b in enumerate(boundaries)]
    return chapters


# Accepts either "HH:MM:SS(.ms) Title", "MM:SS Title", or ffmetadata blocks,
# or a simple ";FFMETADATA1" file, or "seconds Title".
_TS_LINE = re.compile(
    r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2}(?:\.\d+)?)\s+(.*\S)\s*$"
)
_SEC_LINE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s+(.*\S)\s*$")


def parse_chapters_file(path: str) -> list:
    """Parse a chapters sidecar file.

    Supported line formats (auto-detected):
      00:00:00 Introduction
      1:02:33.5 Chapter Twelve
      12:05 Some chapter
      754.2 Named by seconds
    Blank lines and lines starting with '#' or ';' are ignored.
    """
    if not os.path.isfile(path):
        raise ChapterizeError(f"Chapters file not found: {path}")
    chapters: list = []
    with open(path, "r", encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith(("#", ";")):
                continue
            m = _TS_LINE.match(line)
            if m:
                h = int(m.group(1) or 0)
                mnt = int(m.group(2))
                sec = float(m.group(3))
                start = h * 3600 + mnt * 60 + sec
                chapters.append(Chapter(start=start, title=m.group(4)))
                continue
            m = _SEC_LINE.match(line)
            if m:
                chapters.append(Chapter(start=float(m.group(1)), title=m.group(2)))
                continue
            raise ChapterizeError(f"Could not parse chapters line: {line!r}")
    if not chapters:
        raise ChapterizeError(f"No chapters parsed from {path}")
    chapters.sort(key=lambda c: c.start)
    return chapters


# ---------------------------------------------------------------------------
# Official chapter markers via Audnexus (Audible's public metadata mirror)
# ---------------------------------------------------------------------------
AUDNEXUS_BASE = "https://api.audnex.us"
AUDIBLE_CATALOG = "https://api.audible.com/1.0/catalog/products"
_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


def _http_json(url: str, *, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "chapterize/audnexus",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Audnexus returns a JSON error body we can surface verbatim.
        try:
            body = json.loads(e.read().decode("utf-8"))
            msg = body.get("error", {})
            msg = msg.get("message") if isinstance(msg, dict) else (msg or body.get("message"))
        except Exception:
            msg = None
        raise ChapterizeError(
            f"Lookup failed ({e.code}) for {url}"
            + (f": {msg}" if msg else "")
        )
    except urllib.error.URLError as e:
        raise ChapterizeError(f"Network error reaching {url}: {e.reason}")


def search_audible(title: str, author: Optional[str] = None, *,
                   region: str = "us", limit: int = 10) -> list:
    """Search Audible's public catalog for an audiobook, newest/most relevant
    first. Returns a list of dicts: {asin, title, authors, narrators,
    runtime_min}. Raises ChapterizeError only on transport failure — an empty
    list just means no match (common when an edition has been delisted)."""
    if not title:
        raise ChapterizeError("search_audible needs a title.")
    kw = title if not author else f"{title} {author}"
    params = {
        "keywords": kw,
        "num_results": str(max(1, min(limit, 50))),
        "products_sort_by": "Relevance",
        "response_groups": "contributors,product_desc,product_attrs",
    }
    host = AUDIBLE_CATALOG if region == "us" else AUDIBLE_CATALOG.replace(
        "api.audible.com", f"api.audible.{_AUDIBLE_TLD.get(region, 'com')}")
    data = _http_json(host + "?" + urllib.parse.urlencode(params))
    out = []
    for p in data.get("products", []):
        out.append({
            "asin": p.get("asin"),
            "title": p.get("title"),
            "authors": [a.get("name", "") for a in (p.get("authors") or [])],
            "narrators": [a.get("name", "") for a in (p.get("narrators") or [])],
            "runtime_min": p.get("runtime_length_min"),
        })
    return out


# Audible marketplace → API TLD (Audnexus uses the same region codes).
_AUDIBLE_TLD = {
    "us": "com", "ca": "ca", "uk": "co.uk", "au": "com.au",
    "fr": "fr", "de": "de", "jp": "co.jp", "it": "it", "in": "in", "es": "es",
}


def fetch_audible_chapters(asin: str, *, region: str = "us",
                           keep_credits: bool = True,
                           progress: Optional[Callable[[str], None]] = None
                           ) -> "tuple[list, Optional[float]]":
    """Fetch official chapter markers for an audiobook by ASIN from Audnexus.

    Returns (chapters, runtime_seconds). runtime_seconds is the audiobook's
    total length per Audible and is meant to be compared against the local
    file's duration — a large mismatch means the ASIN is a *different edition*
    (re-recording, abridgement) whose timestamps won't line up with your file.
    """
    asin = (asin or "").strip().upper()
    if not _ASIN_RE.match(asin):
        raise ChapterizeError(
            f"{asin!r} is not a valid 10-character Audible ASIN.")
    url = f"{AUDNEXUS_BASE}/books/{asin}/chapters?region={urllib.parse.quote(region)}"
    if progress:
        progress(f"fetching official chapters for {asin} ({region})")
    data = _http_json(url)
    raw = data.get("chapters")
    if not raw:
        raise ChapterizeError(
            f"No chapters returned for ASIN {asin}. The edition may not carry "
            "chapter markers, or may be unavailable in this region "
            "(try --region uk/ca/au/de).")
    chapters = []
    for c in raw:
        title = (c.get("title") or "").strip() or "Chapter"
        if not keep_credits and title.lower() in (
                "opening credits", "end credits", "credits"):
            continue
        chapters.append(Chapter(start=c.get("startOffsetMs", 0) / 1000.0,
                                title=title))
    if not chapters:
        raise ChapterizeError(f"All chapters for ASIN {asin} were filtered out.")
    chapters.sort(key=lambda c: c.start)
    runtime_ms = data.get("runtimeLengthMs")
    runtime_s = (runtime_ms / 1000.0) if runtime_ms else None
    return chapters, runtime_s


# ---------------------------------------------------------------------------
# ffmetadata construction
# ---------------------------------------------------------------------------
def _finalize_ends(chapters: list, duration: float) -> list:
    chapters = sorted(chapters, key=lambda c: c.start)
    for i, ch in enumerate(chapters):
        if ch.end is None:
            ch.end = chapters[i + 1].start if i + 1 < len(chapters) else duration
        # guard against zero/negative-length chapters
        if ch.end <= ch.start:
            ch.end = ch.start + 0.001
    return chapters


def _escape_meta(value: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", value)


def build_ffmetadata(chapters: list, duration: float, tags: dict) -> str:
    lines = [";FFMETADATA1"]
    for key in ("title", "artist", "album", "album_artist", "genre",
                "date", "comment"):
        if tags.get(key):
            lines.append(f"{key}={_escape_meta(str(tags[key]))}")
    for ch in _finalize_ends(list(chapters), duration):
        lines.append("")
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={int(round(ch.start * 1000))}")
        lines.append(f"END={int(round(ch.end * 1000))}")
        lines.append(f"title={_escape_meta(ch.title)}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------
CONTAINER_EXT = {
    "mka": "mka",   # Matroska audio (default; open, first-class chapters)
    "opus": "opus", # Ogg/Opus (chapters as VorbisComment; less universally read)
    "m4b": "m4b",   # MPEG-4 audiobook (AAC); best player compatibility
}

CODEC_FOR = {
    "mka": "libopus",
    "opus": "libopus",
    "m4b": "aac",
}


def convert(input_path: str,
            output_path: str,
            chapters: list,
            *,
            container: str = "mka",
            bitrate: str = "64k",
            tags: Optional[dict] = None,
            cover_path: Optional[str] = None,
            keep_source_cover: bool = True,
            fetch_cover: bool = False,
            generate_cover: bool = False,
            ffmpeg: Optional[str] = None,
            ffprobe: Optional[str] = None,
            overwrite: bool = False,
            progress: Optional[Callable[[str], None]] = None) -> str:
    """Transcode `input_path` to `output_path` embedding `chapters`.

    If the source has no cover art and none is supplied via `cover_path`,
    `fetch_cover=True` will look one up online (by title/author) and
    `generate_cover=True` will render a text placeholder as a last resort.

    Returns the output path on success.
    """
    if container not in CONTAINER_EXT:
        raise ChapterizeError(
            f"Unknown container {container!r}; choose from {sorted(CONTAINER_EXT)}")
    if not overwrite and os.path.exists(output_path):
        raise ChapterizeError(
            f"Output exists: {output_path} (use --overwrite to replace)")
    if not chapters:
        raise ChapterizeError("No chapters to write — refusing to produce a file.")

    info = probe(input_path, ffprobe)
    tags = {**info.tags, **(tags or {})}
    # A sensible default title from the filename if none present.
    tags.setdefault("title", os.path.splitext(os.path.basename(input_path))[0])

    # --- resolve missing cover art -----------------------------------
    _fetched_cover = None
    need_art = not cover_path and not (keep_source_cover and info.has_cover)
    if need_art and (fetch_cover or generate_cover):
        from . import coverart  # local import; stdlib-only, no hard dep
        title = tags.get("title", "")
        author = tags.get("artist") or tags.get("album_artist") or ""
        if fetch_cover:
            if progress:
                progress("fetching cover art…")
            _fetched_cover = coverart.find_cover(title, author)
        if not _fetched_cover and generate_cover:
            if progress:
                progress("generating placeholder cover…")
            _fetched_cover = coverart.generate_placeholder(
                title, author, ffmpeg=ffmpeg_bin(ffmpeg))
        if _fetched_cover:
            cover_path = _fetched_cover
            if progress:
                progress("cover art acquired" if fetch_cover else "placeholder made")

    meta_text = build_ffmetadata(chapters, info.duration, tags)
    exe = ffmpeg_bin(ffmpeg)

    with tempfile.NamedTemporaryFile("w", suffix=".ffmeta", delete=False,
                                     encoding="utf-8") as mf:
        mf.write(meta_text)
        meta_file = mf.name

    try:
        cmd = [exe, "-hide_banner", "-y" if overwrite else "-n",
               "-i", input_path, "-i", meta_file]

        cover_input_idx = None
        if cover_path:
            cmd += ["-i", cover_path]
            cover_input_idx = 2

        # metadata (incl. chapters) comes from input #1
        cmd += ["-map_metadata", "1", "-map_chapters", "1"]
        # audio from input #0
        cmd += ["-map", "0:a:0"]

        codec = CODEC_FOR[container]
        cmd += ["-c:a", codec, "-b:a", bitrate]

        if container == "m4b":
            # audiobook-friendly defaults
            cmd += ["-movflags", "+faststart"]
            if cover_input_idx is not None:
                cmd += ["-map", f"{cover_input_idx}:v:0", "-c:v", "mjpeg",
                        "-disposition:v:0", "attached_pic"]
            elif keep_source_cover and info.has_cover:
                cmd += ["-map", "0:v:0?", "-c:v", "copy",
                        "-disposition:v:0", "attached_pic"]
        elif container == "mka":
            if cover_input_idx is not None:
                cmd += ["-map", f"{cover_input_idx}:v:0", "-c:v", "copy",
                        "-disposition:v:0", "attached_pic"]
            elif keep_source_cover and info.has_cover:
                cmd += ["-map", "0:v:0?", "-c:v", "copy",
                        "-disposition:v:0", "attached_pic"]

        if container == "opus":
            # Ogg/Opus cover-art embedding is poorly supported; drop video.
            cmd += ["-vn"]

        cmd.append(output_path)

        if progress:
            progress("running ffmpeg…")
        proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                text=True)
        tail: list = []
        assert proc.stderr is not None
        for line in proc.stderr:
            tail.append(line.rstrip())
            if len(tail) > 40:
                tail.pop(0)
            if progress and ("time=" in line):
                m = re.search(r"time=(\S+)", line)
                if m:
                    progress(f"encoding… {m.group(1)}")
        proc.wait()
        if proc.returncode != 0:
            raise ChapterizeError(
                "ffmpeg failed:\n" + "\n".join(tail[-20:]))
    finally:
        for tmp in (meta_file, _fetched_cover):
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    if progress:
        progress("done")
    return output_path


# ---------------------------------------------------------------------------
# Add cover art to an existing file WITHOUT re-encoding (remux)
# ---------------------------------------------------------------------------
def add_cover(input_path: str,
              cover_path: str,
              output_path: Optional[str] = None,
              *,
              ffmpeg: Optional[str] = None,
              ffprobe: Optional[str] = None,
              in_place: bool = False,
              progress: Optional[Callable[[str], None]] = None) -> str:
    """Embed `cover_path` into `input_path` by stream-copy (no transcode).

    Works for MP4/M4B/M4A and Matroska. If `in_place`, the original is
    atomically replaced. Returns the written path.
    """
    exe = ffmpeg_bin(ffmpeg)
    ext = os.path.splitext(input_path)[1].lower()
    if ext in (".opus", ".ogg", ".oga"):
        raise ChapterizeError(
            "Ogg/Opus does not support attached-picture cover art; "
            "re-run convert to another container to add art.")

    if in_place:
        fd, tmp_out = tempfile.mkstemp(suffix=ext, dir=os.path.dirname(input_path) or ".")
        os.close(fd)
        target = tmp_out
    else:
        target = output_path or (os.path.splitext(input_path)[0] + ".cover" + ext)
        tmp_out = None

    # Map audio only (not the source's chapter *text* track — the ipod/mp4
    # muxer rejects copying that as a stream) plus the new cover image.
    # Chapters are carried losslessly by -map_chapters, independent of the
    # stream map. Any pre-existing attached_pic is dropped and replaced.
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y",
           "-i", input_path, "-i", cover_path,
           "-map", "0:a", "-map", "1:v:0",
           "-map_metadata", "0", "-map_chapters", "0",
           "-c:a", "copy"]
    codec = "mjpeg" if cover_path.lower().endswith((".jpg", ".jpeg")) else "png"
    cmd += ["-c:v", codec if ext in (".m4b", ".m4a", ".mp4") else "copy",
            "-disposition:v:0", "attached_pic"]
    if ext in (".m4b", ".m4a", ".mp4"):
        cmd += ["-movflags", "+faststart"]
    cmd.append(target)

    if progress:
        progress("embedding cover…")
    proc = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        if tmp_out and os.path.exists(tmp_out):
            os.unlink(tmp_out)
        raise ChapterizeError("ffmpeg cover embed failed:\n" + (proc.stderr or "")[-800:])

    if in_place:
        os.replace(tmp_out, input_path)
        return input_path
    return target
