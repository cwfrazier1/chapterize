"""chapterize — convert long audio files into chaptered open-source audiobooks.

Target format: Opus audio in a Matroska (.mka) container. Both the Opus codec
and the Matroska container are open, royalty-free formats, and ffmpeg writes
real chapter markers into Matroska reliably. Optional .m4b / .opus output is
supported for players that expect them.
"""

__version__ = "0.1.0"

from .core import (  # noqa: F401
    Chapter,
    ChapterizeError,
    convert,
    detect_silence_chapters,
    equal_chapters,
    probe,
    read_embedded_chapters,
    parse_chapters_file,
)
