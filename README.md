# chapterize

Convert long audio files into **chaptered, fully open-source audiobooks**.

The target format is **Opus audio inside a Matroska (`.mka`) container** — both
the [Opus](https://opus-codec.org/) codec and the
[Matroska](https://www.matroska.org/) container are open, royalty-free
formats, and (unlike Ogg/Opus) ffmpeg writes real chapter markers into
Matroska reliably. `.m4b` (AAC) and `.opus` (Ogg) output are also supported
for players that expect them.

Runs on **macOS and Linux**, as both a command-line tool and a small GUI.
It's a thin, dependency-free Python wrapper around `ffmpeg`/`ffprobe`.

---

## Install

```bash
git clone https://github.com/cwfrazier1/chapterize.git
cd chapterize
./install.sh          # installs ffmpeg if missing, then the app into ~/.local
```

Or manually (you provide ffmpeg via `brew install ffmpeg` /
`apt install ffmpeg python3-tk`):

```bash
pip install .
```

`chapterize` needs **ffmpeg** and **ffprobe** on your `PATH`.

---

## GUI

```bash
chapterize-gui
```

Pick an input file, choose how chapters are found (silence / embedded /
equal parts / a chapters file), set title, author and cover, hit **Preview
chapters** to check, then **Convert**.

## CLI

```bash
# Inspect a file and any embedded chapters
chapterize probe audiobook.mp3

# Auto-detect chapters at long pauses → Opus/Matroska .mka (default)
chapterize convert bigbook.mp3 -c silence

# Split a 10-hour recording into 24 equal chapters, as .m4b
chapterize convert lecture.wav -f m4b -c equal --count 24

# Use a chapters sidecar file, set metadata + cover
chapterize convert book.m4a -c chapters.txt \
    --title "My Book" --author "Jane Doe" --cover cover.jpg

# Preview only — list chapters without encoding
chapterize convert book.mp3 -c silence --dry-run
```

### Chapter sources (`-c` / `--chapters`)

| Value        | Meaning                                                            |
|--------------|-------------------------------------------------------------------|
| `embedded`   | Copy chapters already present in the source (default)             |
| `silence`    | Detect boundaries at long silences (tune with `--silence-*`)      |
| `equal`      | Split into `--count N` equal parts, or `--length SECONDS` parts   |
| *a file path*| Read a chapters sidecar file                                       |

### Chapters file format

One chapter per line — timestamp then title. Blank lines and lines starting
with `#` or `;` are ignored. Timestamps auto-detected:

```
# Comments allowed
00:00:00      Introduction
12:05         A shorter timestamp (MM:SS)
1:02:33.5     Fractional seconds
3800          Plain seconds
```

### Silence-detection tuning

- `--silence-db` — loudness threshold (default `-30`; more negative = quieter counts as silence)
- `--silence-dur` — minimum pause length in seconds to treat as a boundary (default `0.8`)
- `--min-chapter` — minimum seconds between chapters, so a noisy file doesn't shatter into hundreds (default `90`)

### Output formats (`-f`)

| Format | Container | Codec | Notes                                             |
|--------|-----------|-------|---------------------------------------------------|
| `mka`  | Matroska  | Opus  | **Default.** Fully open, reliable chapters.       |
| `opus` | Ogg       | Opus  | Chapters via VorbisComment (less widely read).    |
| `m4b`  | MP4       | AAC   | Best player compatibility (Apple Books, etc).     |

---

## Development

```bash
python3 tests/test_core.py     # pure-Python unit tests (no ffmpeg needed)
```

The conversion path is exercised end-to-end against a real ffmpeg during
development; the unit tests cover chapter parsing, equal-split math, and
ffmetadata generation.

## License

MIT — see [LICENSE](LICENSE).
