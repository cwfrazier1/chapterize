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

Pick an input file, give the Audible ASIN (or a title/author to look it up),
set title, author and cover, hit **Preview chapters** to check, then
**Convert**.

> **macOS note:** the GUI needs **Tk 8.6 or newer**. The Python that ships
> at `/usr/bin/python3` on macOS bundles Apple's deprecated **Tk 8.5**, which
> renders Tk windows *blank* on modern macOS. Run the GUI under a python.org
> installer build, a Homebrew `python-tk`, or any standalone CPython (all
> bundle Tk 8.6+). chapterize detects Tk 8.5 and shows a native warning
> dialog. The **CLI has no such limitation** and works under any Python.

## CLI

```bash
# Inspect a file and any embedded chapters
chapterize probe audiobook.mp3

# Chapter a flat file with the publisher's REAL markers, by ASIN
chapterize convert book.mp3 --asin B0D7WF35B4

# ...or let it find the ASIN by searching Audible for the title/author
chapterize convert book.mp3 --title "Dead Med" --author "Freida McFadden"

# Just look up a book's official chapters (no conversion)
chapterize audible "Dead Med" --author "Freida McFadden"
chapterize audible --asin B0D7WF35B4          # exact edition
chapterize audible "Project Hail Mary" --search   # list editions + ASINs

# Preview only — list chapters without encoding
chapterize convert book.mp3 --asin B0D7WF35B4 --dry-run
```

### Chapter source — official Audible markers

chapterize chapters a flat file from the **publisher's official chapter
markers** on Audible (via the public [Audnexus](https://audnex.us) mirror) —
the exact chapter titles and timestamps the publisher shipped. This is the
**only** chapter source: silence-detection and equal-split only *guess* at
boundaries, so they're gone.

Give it the ASIN directly (`--asin`), or leave it off and chapterize searches
Audible by `--title`/`--author` (falling back to the file's own tags).

> The catch: the ASIN's runtime must match your file. Audiobooks that were
> re-recorded or re-released (a different narrator, a revised edition) have
> **different** timestamps — chapterize warns loudly when the Audible runtime
> drifts from your file's, because those markers won't line up. Pick the ASIN
> whose length matches your audio (use `chapterize audible … --search` to
> compare editions).

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
