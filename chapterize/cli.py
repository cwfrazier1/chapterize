"""Command-line interface for chapterize."""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__
import glob

from .core import (
    Chapter,
    ChapterizeError,
    add_cover,
    convert,
    detect_silence_chapters,
    equal_chapters,
    parse_chapters_file,
    probe,
)
from .coverart import find_cover, generate_placeholder


def _fmt_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:05.2f}"


def _resolve_chapters(args, info) -> list:
    mode = args.chapters
    if mode == "embedded":
        if not info.chapters:
            raise ChapterizeError(
                "No embedded chapters found. Try --chapters silence|equal|<file>.")
        return info.chapters
    if mode == "silence":
        return detect_silence_chapters(
            args.input,
            noise_db=args.silence_db,
            min_silence=args.silence_dur,
            min_chapter=args.min_chapter,
            ffmpeg=args.ffmpeg,
            progress=(lambda m: print(f"  {m}", file=sys.stderr)) if args.verbose else None,
        )
    if mode == "equal":
        return equal_chapters(
            info.duration,
            count=args.count,
            length=args.length,
        )
    # Otherwise treat it as a path to a chapters file.
    if os.path.isfile(mode):
        return parse_chapters_file(mode)
    raise ChapterizeError(
        f"--chapters {mode!r} is neither a known mode nor an existing file.")


def cmd_probe(args) -> int:
    info = probe(args.input, ffprobe=args.ffprobe)
    print(f"Duration : {_fmt_ts(info.duration)} ({info.duration:.1f}s)")
    print(f"Codec    : {info.codec}  {info.channels}ch  {info.sample_rate}Hz")
    if info.bit_rate:
        print(f"Bitrate  : {info.bit_rate // 1000} kbps")
    print(f"Cover    : {'yes' if info.has_cover else 'no'}")
    if info.tags:
        for k in ("title", "artist", "album", "date"):
            if info.tags.get(k):
                print(f"{k:9}: {info.tags[k]}")
    print(f"Chapters : {len(info.chapters)}")
    for i, ch in enumerate(info.chapters, 1):
        print(f"  {i:>3}. {_fmt_ts(ch.start)}  {ch.title}")
    return 0


def cmd_convert(args) -> int:
    info = probe(args.input, ffprobe=args.ffprobe)
    chapters = _resolve_chapters(args, info)

    if not args.output:
        base = os.path.splitext(args.input)[0]
        args.output = f"{base}.{args.format}"

    if args.dry_run:
        print(f"Would write {len(chapters)} chapters to {args.output} "
              f"({args.format}, {args.bitrate}):")
        for i, ch in enumerate(chapters, 1):
            print(f"  {i:>3}. {_fmt_ts(ch.start)}  {ch.title}")
        return 0

    tags = {}
    if args.title:
        tags["title"] = args.title
    if args.author:
        tags["artist"] = args.author
        tags["album_artist"] = args.author

    def prog(msg):
        print(f"\r  {msg:<60}", end="", file=sys.stderr, flush=True)

    print(f"Converting → {args.output}  "
          f"[{len(chapters)} chapters, {args.format}, {args.bitrate}]",
          file=sys.stderr)
    convert(
        args.input,
        args.output,
        chapters,
        container=args.format,
        bitrate=args.bitrate,
        tags=tags or None,
        cover_path=args.cover,
        fetch_cover=args.fetch_cover,
        generate_cover=args.generate_cover,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
        overwrite=args.overwrite,
        progress=prog if not args.quiet else None,
    )
    print(f"\nDone: {args.output}", file=sys.stderr)
    return 0


def _guess_title_author(path, info):
    """Prefer embedded tags; fall back to Author/Title/file.ext layout."""
    title = info.tags.get("title")
    author = info.tags.get("artist") or info.tags.get("album_artist")
    parent = os.path.basename(os.path.dirname(path))
    grandparent = os.path.basename(os.path.dirname(os.path.dirname(path)))
    if not title:
        title = parent or os.path.splitext(os.path.basename(path))[0]
    if not author and grandparent and grandparent.lower() not in ("", "books", "audiobooks"):
        author = grandparent
    return title, (author or "")


def _iter_audio(paths, recursive):
    exts = (".mp3", ".m4a", ".m4b", ".mp4", ".flac", ".opus", ".ogg", ".aac", ".wav")
    for p in paths:
        if os.path.isdir(p):
            walker = os.walk(p) if recursive else [(p, [], os.listdir(p))]
            for root, _dirs, files in walker:
                if "@eaDir" in root:
                    continue
                for f in sorted(files):
                    if f.lower().endswith(exts):
                        yield os.path.join(root, f)
        elif any(ch in p for ch in "*?["):
            yield from (g for g in glob.glob(p) if g.lower().endswith(exts))
        else:
            yield p


def cmd_coverfix(args) -> int:
    fixed = skipped = failed = 0
    for path in _iter_audio(args.paths, args.recursive):
        try:
            info = probe(path, ffprobe=args.ffprobe)
        except ChapterizeError as e:
            print(f"skip  {path}: {e}", file=sys.stderr)
            failed += 1
            continue
        if info.has_cover and not args.force:
            skipped += 1
            if args.verbose:
                print(f"has   {path}")
            continue
        title, author = _guess_title_author(path, info)
        cover = None
        if not args.generate_only:
            cover = find_cover(title, author)
        generated = False
        if not cover and (args.generate or args.generate_only):
            cover = generate_placeholder(title, author, ffmpeg=args.ffmpeg)
            generated = True
        if not cover:
            print(f"none  {path}  (no art for {title!r} / {author!r})")
            failed += 1
            continue
        src = "generated" if generated else "fetched"
        if args.dry_run:
            print(f"would {src}+embed  {title!r} ← {os.path.basename(cover)}  [{path}]")
            os.unlink(cover)
            fixed += 1
            continue
        try:
            add_cover(path, cover, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
                      in_place=True)
            print(f"fixed [{src}]  {title}")
            fixed += 1
        except ChapterizeError as e:
            print(f"fail  {path}: {e}", file=sys.stderr)
            failed += 1
        finally:
            if cover and os.path.exists(cover):
                os.unlink(cover)
    print(f"\n{fixed} fixed, {skipped} already had art, {failed} failed",
          file=sys.stderr)
    return 0 if failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chapterize",
        description="Convert long audio files into chaptered, open-source "
                    "audiobooks (Opus in Matroska).",
    )
    p.add_argument("--version", action="version",
                   version=f"chapterize {__version__}")
    p.add_argument("--ffmpeg", help="path to ffmpeg binary")
    p.add_argument("--ffprobe", help="path to ffprobe binary")

    sub = p.add_subparsers(dest="command")

    pp = sub.add_parser("probe", help="show media info and any embedded chapters")
    pp.add_argument("input")
    pp.set_defaults(func=cmd_probe)

    pc = sub.add_parser("convert", help="transcode + embed chapters (default)")
    pc.add_argument("input")
    pc.add_argument("-o", "--output", help="output path (default: alongside input)")
    pc.add_argument("-f", "--format", choices=["mka", "opus", "m4b"], default="mka",
                    help="output container (default: mka = open Opus+Matroska)")
    pc.add_argument("-b", "--bitrate", default="64k",
                    help="audio bitrate (default: 64k, good for speech)")
    pc.add_argument("-c", "--chapters", default="embedded",
                    metavar="MODE|FILE",
                    help="embedded | silence | equal | <path to chapters file>")
    # silence options
    pc.add_argument("--silence-db", type=float, default=-30.0,
                    help="silence threshold in dB (default: -30)")
    pc.add_argument("--silence-dur", type=float, default=0.8,
                    help="min silence length in s to count (default: 0.8)")
    pc.add_argument("--min-chapter", type=float, default=90.0,
                    help="min seconds between silence-based chapters (default: 90)")
    # equal options
    pc.add_argument("--count", type=int, help="split into N equal chapters")
    pc.add_argument("--length", type=float, help="split into fixed-length chapters (s)")
    # metadata
    pc.add_argument("--title", help="override audiobook title")
    pc.add_argument("--author", help="set author/artist")
    pc.add_argument("--cover", help="cover image to embed (jpg/png)")
    pc.add_argument("--fetch-cover", action="store_true",
                    help="if source has no cover, look one up online by title/author")
    pc.add_argument("--generate-cover", action="store_true",
                    help="if no cover found, render a text placeholder cover")
    pc.add_argument("--overwrite", action="store_true", help="replace existing output")
    pc.add_argument("--dry-run", action="store_true", help="list chapters, don't encode")
    pc.add_argument("-q", "--quiet", action="store_true")
    pc.add_argument("-v", "--verbose", action="store_true")
    pc.set_defaults(func=cmd_convert)

    cx = sub.add_parser("coverfix",
                        help="add missing cover art to existing files / a library")
    cx.add_argument("paths", nargs="+",
                    help="audio files, directories, or globs")
    cx.add_argument("-r", "--recursive", action="store_true",
                    help="recurse into directories")
    cx.add_argument("--generate", action="store_true",
                    help="render a placeholder cover if none is found online")
    cx.add_argument("--generate-only", action="store_true",
                    help="skip online lookup; always render a placeholder")
    cx.add_argument("--force", action="store_true",
                    help="re-embed art even if the file already has some")
    cx.add_argument("--dry-run", action="store_true",
                    help="report what would be fixed without modifying files")
    cx.add_argument("-v", "--verbose", action="store_true")
    cx.set_defaults(func=cmd_coverfix)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except ChapterizeError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\naborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
