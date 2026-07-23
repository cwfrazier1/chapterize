"""Headless functional test of the Tk GUI's convert path.

Instantiates the real App, drives its StringVars/BooleanVars the way a user
would, fires App._run(), and pumps the Tk event loop until the worker thread
finishes — then verifies the output file exists and has chapters + cover.

Must run inside a macOS/Linux GUI session (Tk needs a display connection).
Writes a clear PASS/FAIL summary to stdout. Exit 0 on pass.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

IN = os.environ.get("GUI_TEST_INPUT", "/tmp/gui_in.m4b")
OUT = os.environ.get("GUI_TEST_OUTPUT", "/tmp/gui_out.mka")
FF = os.environ.get("GUI_TEST_FFMPEG")      # optional ffmpeg path
FP = os.environ.get("GUI_TEST_FFPROBE")     # optional ffprobe path


def main() -> int:
    import tkinter as tk
    from chapterize import gui, probe

    # Modal dialogs would block the headless pump loop — stub them out.
    gui.messagebox.showinfo = lambda *a, **k: None
    gui.messagebox.showerror = lambda *a, **k: None
    gui.messagebox.showwarning = lambda *a, **k: None

    if os.path.exists(OUT):
        os.unlink(OUT)

    root = tk.Tk()
    root.withdraw()  # no need to show the window for a functional test
    app = gui.App(root)

    app.in_var.set(IN)
    app.out_var.set(OUT)
    app.fmt_var.set("mka")
    app.br_var.set("48k")
    app.mode_var.set("equal")
    app._mode_changed()
    app.count_var.set("2")
    app.title_var.set("Dune")
    app.author_var.set("Frank Herbert")
    app.fetch_cover_var.set(True)

    app._run()

    deadline = time.time() + 90
    ok = False
    while time.time() < deadline:
        root.update()
        if os.path.exists(OUT) and os.path.getsize(OUT) > 1024:
            # give ffmpeg a beat to finalise the container
            time.sleep(1)
            ok = True
            break
        time.sleep(0.2)

    if not ok:
        print("FAIL: output not produced by GUI within timeout")
        return 1

    info = probe(OUT, ffprobe=FP)
    print(f"GUI produced: {OUT}  chapters={len(info.chapters)} cover={info.has_cover}")
    if len(info.chapters) != 2:
        print(f"FAIL: expected 2 chapters, got {len(info.chapters)}")
        return 1
    if not info.has_cover:
        print("FAIL: fetch-cover checkbox did not embed cover art")
        return 1
    print("PASS: GUI convert path works (chapters + fetched cover)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
