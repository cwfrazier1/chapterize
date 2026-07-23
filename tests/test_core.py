"""Unit tests for the pure-Python parts (no ffmpeg required)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chapterize.core import (  # noqa: E402
    Chapter,
    ChapterizeError,
    build_ffmetadata,
    equal_chapters,
    parse_chapters_file,
    _finalize_ends,
)


def test_equal_by_count():
    ch = equal_chapters(600, count=4)
    assert len(ch) == 4
    assert ch[0].start == 0
    assert abs(ch[1].start - 150) < 1e-6
    assert ch[-1].title == "Chapter 4"


def test_equal_by_length():
    ch = equal_chapters(650, length=200)
    assert len(ch) == 4  # 200,200,200,50
    assert ch[3].start == 600


def test_equal_bad_input():
    try:
        equal_chapters(0, count=3)
    except ChapterizeError:
        pass
    else:
        raise AssertionError("expected ChapterizeError")


def test_finalize_ends():
    ch = _finalize_ends([Chapter(0, "a"), Chapter(100, "b")], 250)
    assert ch[0].end == 100
    assert ch[1].end == 250


def test_finalize_ends_guards_zero_length():
    ch = _finalize_ends([Chapter(100, "a"), Chapter(100, "b")], 200)
    assert ch[0].end > ch[0].start


def test_parse_various_timestamps():
    text = (
        "# a comment\n"
        "00:00:00 Intro\n"
        "12:05 Middle bit\n"
        "1:02:33.5 Later\n"
        "3800 By seconds\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(text)
        p = f.name
    try:
        ch = parse_chapters_file(p)
    finally:
        os.unlink(p)
    assert [c.title for c in ch] == ["Intro", "Middle bit", "Later", "By seconds"]
    assert ch[0].start == 0
    assert ch[1].start == 12 * 60 + 5
    assert abs(ch[2].start - (3600 + 2 * 60 + 33.5)) < 1e-6
    assert ch[3].start == 3800


def test_parse_bad_line_raises():
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("this is not a chapter line\n")
        p = f.name
    try:
        parse_chapters_file(p)
    except ChapterizeError:
        pass
    else:
        raise AssertionError("expected ChapterizeError")
    finally:
        os.unlink(p)


def test_coverart_image_detection():
    from chapterize.coverart import _looks_like_image
    assert _looks_like_image(b"\xff\xd8\xff\xe0rest") == ".jpg"
    assert _looks_like_image(b"\x89PNG\r\n\x1a\nrest") == ".png"
    assert _looks_like_image(b"<html>not an image") is None


def test_build_ffmetadata_shape():
    meta = build_ffmetadata(
        [Chapter(0, "One"), Chapter(60, "Two=weird;chars")],
        duration=120,
        tags={"title": "My Book", "artist": "Me"},
    )
    assert meta.startswith(";FFMETADATA1")
    assert "title=My Book" in meta
    assert meta.count("[CHAPTER]") == 2
    assert "TIMEBASE=1/1000" in meta
    assert "START=0" in meta
    assert "END=60000" in meta
    # special chars in title are escaped
    assert "Two\\=weird\\;chars" in meta


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
