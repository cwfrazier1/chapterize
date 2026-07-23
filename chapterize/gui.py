"""Tkinter GUI for chapterize.

Cross-platform (macOS + Linux). Runs the actual conversion on a worker
thread so the UI stays responsive. Tkinter ships with CPython on macOS
(python.org builds) and is available on Linux via the system 'python3-tk'
package.
"""
from __future__ import annotations

import os
import queue
import threading

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Tkinter is not available. Install it:\n"
        "  Debian/Ubuntu: sudo apt install python3-tk\n"
        "  Fedora:        sudo dnf install python3-tkinter\n"
        "  macOS:         use the python.org installer (bundles Tk)\n"
        f"(underlying error: {exc})"
    )

from .core import (
    ChapterizeError,
    convert,
    detect_silence_chapters,
    equal_chapters,
    parse_chapters_file,
    probe,
)

AUDIO_TYPES = [
    ("Audio files", "*.mp3 *.m4a *.m4b *.aac *.wav *.flac *.ogg *.opus *.wma *.aiff"),
    ("All files", "*.*"),
]


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=12)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.info = None
        self._chapters = []

        self.in_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.fmt_var = tk.StringVar(value="mka")
        self.br_var = tk.StringVar(value="64k")
        self.mode_var = tk.StringVar(value="silence")
        self.file_var = tk.StringVar()
        self.count_var = tk.StringVar(value="")
        self.length_var = tk.StringVar(value="")
        self.title_var = tk.StringVar()
        self.author_var = tk.StringVar()
        self.cover_var = tk.StringVar()

        self._build()
        self.after(120, self._pump)

    # -- layout --------------------------------------------------------
    def _row(self, r, label):
        ttk.Label(self, text=label).grid(row=r, column=0, sticky="w", pady=3)

    def _build(self):
        r = 0
        self._row(r, "Input audio")
        e = ttk.Entry(self, textvariable=self.in_var)
        e.grid(row=r, column=1, sticky="ew", padx=6)
        ttk.Button(self, text="Browse…", command=self._pick_input).grid(row=r, column=2)

        r += 1
        self._row(r, "Output file")
        ttk.Entry(self, textvariable=self.out_var).grid(row=r, column=1, sticky="ew", padx=6)
        ttk.Button(self, text="Save as…", command=self._pick_output).grid(row=r, column=2)

        r += 1
        self._row(r, "Format")
        fr = ttk.Frame(self)
        fr.grid(row=r, column=1, sticky="w", padx=6)
        for txt, val in [("Opus/Matroska .mka (open)", "mka"),
                         ("Ogg/Opus .opus", "opus"),
                         ("M4B (AAC)", "m4b")]:
            ttk.Radiobutton(fr, text=txt, value=val, variable=self.fmt_var,
                            command=self._sync_out_ext).pack(side="left", padx=(0, 8))

        r += 1
        self._row(r, "Bitrate")
        ttk.Combobox(self, textvariable=self.br_var, width=10,
                     values=["32k", "48k", "64k", "96k", "128k"]).grid(
            row=r, column=1, sticky="w", padx=6)

        r += 1
        self._row(r, "Chapters from")
        cf = ttk.Frame(self)
        cf.grid(row=r, column=1, columnspan=2, sticky="ew", padx=6)
        for txt, val in [("Detect by silence", "silence"),
                         ("Embedded", "embedded"),
                         ("Equal parts", "equal"),
                         ("Chapters file", "file")]:
            ttk.Radiobutton(cf, text=txt, value=val, variable=self.mode_var,
                            command=self._mode_changed).pack(side="left", padx=(0, 8))

        r += 1
        self.opt_frame = ttk.Frame(self)
        self.opt_frame.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(2, 4))

        r += 1
        self._row(r, "Title")
        ttk.Entry(self, textvariable=self.title_var).grid(row=r, column=1, columnspan=2,
                                                          sticky="ew", padx=6)
        r += 1
        self._row(r, "Author")
        ttk.Entry(self, textvariable=self.author_var).grid(row=r, column=1, columnspan=2,
                                                           sticky="ew", padx=6)
        r += 1
        self._row(r, "Cover image")
        ttk.Entry(self, textvariable=self.cover_var).grid(row=r, column=1, sticky="ew", padx=6)
        ttk.Button(self, text="Browse…", command=self._pick_cover).grid(row=r, column=2)

        r += 1
        btns = ttk.Frame(self)
        btns.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        self.preview_btn = ttk.Button(btns, text="Preview chapters", command=self._preview)
        self.preview_btn.pack(side="left")
        self.run_btn = ttk.Button(btns, text="Convert", command=self._run)
        self.run_btn.pack(side="left", padx=6)

        r += 1
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(4, 2))

        r += 1
        self.log = tk.Text(self, height=12, wrap="none")
        self.log.grid(row=r, column=0, columnspan=3, sticky="nsew", pady=(4, 0))
        self.rowconfigure(r, weight=1)
        self.log.configure(state="disabled")

        self._mode_changed()

    # -- option sub-panel ---------------------------------------------
    def _mode_changed(self):
        for w in self.opt_frame.winfo_children():
            w.destroy()
        m = self.mode_var.get()
        if m == "silence":
            ttk.Label(self.opt_frame,
                      text="Auto-splits at long pauses. Tune if you get too "
                           "many/few chapters:").pack(anchor="w")
            row = ttk.Frame(self.opt_frame); row.pack(anchor="w", pady=2)
            self.db_var = getattr(self, "db_var", tk.StringVar(value="-30"))
            self.dur_var = getattr(self, "dur_var", tk.StringVar(value="0.8"))
            self.minch_var = getattr(self, "minch_var", tk.StringVar(value="90"))
            for lbl, var, w in [("threshold dB", self.db_var, 6),
                                ("min silence s", self.dur_var, 6),
                                ("min chapter s", self.minch_var, 6)]:
                ttk.Label(row, text=lbl).pack(side="left")
                ttk.Entry(row, textvariable=var, width=w).pack(side="left", padx=(2, 10))
        elif m == "equal":
            row = ttk.Frame(self.opt_frame); row.pack(anchor="w", pady=2)
            ttk.Label(row, text="Number of chapters").pack(side="left")
            ttk.Entry(row, textvariable=self.count_var, width=6).pack(side="left", padx=6)
            ttk.Label(row, text="  or fixed length (s)").pack(side="left")
            ttk.Entry(row, textvariable=self.length_var, width=8).pack(side="left", padx=6)
        elif m == "file":
            row = ttk.Frame(self.opt_frame); row.pack(anchor="w", pady=2)
            ttk.Label(row, text="Chapters file").pack(side="left")
            ttk.Entry(row, textvariable=self.file_var, width=40).pack(side="left", padx=6)
            ttk.Button(row, text="Browse…", command=self._pick_chfile).pack(side="left")
        else:
            ttk.Label(self.opt_frame,
                      text="Uses chapters already embedded in the source file.").pack(anchor="w")

    # -- file pickers --------------------------------------------------
    def _pick_input(self):
        path = filedialog.askopenfilename(title="Choose audio file", filetypes=AUDIO_TYPES)
        if path:
            self.in_var.set(path)
            self._sync_out_ext()
            self._load_info(path)

    def _pick_output(self):
        ext = self.fmt_var.get()
        path = filedialog.asksaveasfilename(defaultextension=f".{ext}",
                                            filetypes=[(ext, f"*.{ext}")])
        if path:
            self.out_var.set(path)

    def _pick_cover(self):
        path = filedialog.askopenfilename(
            title="Cover image",
            filetypes=[("Images", "*.jpg *.jpeg *.png"), ("All", "*.*")])
        if path:
            self.cover_var.set(path)

    def _pick_chfile(self):
        path = filedialog.askopenfilename(title="Chapters file",
                                          filetypes=[("Text", "*.txt *.chapters *.cue"),
                                                     ("All", "*.*")])
        if path:
            self.file_var.set(path)

    def _sync_out_ext(self):
        inp = self.in_var.get()
        if not inp:
            return
        base = os.path.splitext(self.out_var.get() or inp)[0]
        self.out_var.set(f"{base}.{self.fmt_var.get()}")

    # -- info / preview ------------------------------------------------
    def _load_info(self, path):
        def work():
            try:
                info = probe(path)
                self.q.put(("info", info))
            except ChapterizeError as e:
                self.q.put(("error", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _collect_mode_args(self):
        m = self.mode_var.get()
        return m

    def _compute_chapters(self):
        m = self.mode_var.get()
        info = self.info
        if info is None:
            info = probe(self.in_var.get())
            self.info = info
        if m == "embedded":
            if not info.chapters:
                raise ChapterizeError("No embedded chapters in this file.")
            return info.chapters
        if m == "silence":
            return detect_silence_chapters(
                self.in_var.get(),
                noise_db=float(self.db_var.get()),
                min_silence=float(self.dur_var.get()),
                min_chapter=float(self.minch_var.get()),
                progress=lambda msg: self.q.put(("log", msg)),
            )
        if m == "equal":
            count = int(self.count_var.get()) if self.count_var.get().strip() else None
            length = float(self.length_var.get()) if self.length_var.get().strip() else None
            if not count and not length:
                raise ChapterizeError("Enter a chapter count or a fixed length.")
            return equal_chapters(info.duration, count=count, length=length)
        if m == "file":
            if not self.file_var.get():
                raise ChapterizeError("Choose a chapters file.")
            return parse_chapters_file(self.file_var.get())
        raise ChapterizeError("Unknown chapter mode.")

    def _preview(self):
        if not self.in_var.get():
            messagebox.showwarning("chapterize", "Choose an input file first.")
            return
        self._busy(True, "detecting chapters…")

        def work():
            try:
                chapters = self._compute_chapters()
                self.q.put(("chapters", chapters))
            except ChapterizeError as e:
                self.q.put(("error", str(e)))
            finally:
                self.q.put(("busy", False))
        threading.Thread(target=work, daemon=True).start()

    def _run(self):
        if not self.in_var.get():
            messagebox.showwarning("chapterize", "Choose an input file first.")
            return
        if not self.out_var.get():
            self._sync_out_ext()
        self._busy(True, "converting…")

        def work():
            try:
                chapters = self._compute_chapters()
                self.q.put(("log", f"{len(chapters)} chapters"))
                tags = {}
                if self.title_var.get():
                    tags["title"] = self.title_var.get()
                if self.author_var.get():
                    tags["artist"] = self.author_var.get()
                    tags["album_artist"] = self.author_var.get()
                out = convert(
                    self.in_var.get(),
                    self.out_var.get(),
                    chapters,
                    container=self.fmt_var.get(),
                    bitrate=self.br_var.get(),
                    tags=tags or None,
                    cover_path=self.cover_var.get() or None,
                    overwrite=True,
                    progress=lambda msg: self.q.put(("log", msg)),
                )
                self.q.put(("done", out))
            except ChapterizeError as e:
                self.q.put(("error", str(e)))
            finally:
                self.q.put(("busy", False))
        threading.Thread(target=work, daemon=True).start()

    # -- queue pump ----------------------------------------------------
    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "info":
                    self.info = payload
                    self._log(f"Loaded: {payload.duration:.0f}s, {payload.codec}, "
                              f"{len(payload.chapters)} embedded chapters")
                    if payload.tags.get("title") and not self.title_var.get():
                        self.title_var.set(payload.tags["title"])
                    if payload.tags.get("artist") and not self.author_var.get():
                        self.author_var.set(payload.tags["artist"])
                elif kind == "chapters":
                    self._chapters = payload
                    self._log(f"--- {len(payload)} chapters ---")
                    for i, ch in enumerate(payload, 1):
                        self._log(f"{i:>3}. {self._ts(ch.start)}  {ch.title}")
                elif kind == "log":
                    self._log(str(payload))
                elif kind == "error":
                    self._busy(False)
                    messagebox.showerror("chapterize", str(payload))
                elif kind == "done":
                    self._busy(False)
                    self._log(f"Done → {payload}")
                    messagebox.showinfo("chapterize", f"Saved:\n{payload}")
                elif kind == "busy":
                    self._busy(bool(payload))
        except queue.Empty:
            pass
        self.after(120, self._pump)

    # -- helpers -------------------------------------------------------
    @staticmethod
    def _ts(seconds: float) -> str:
        h = int(seconds // 3600); m = int((seconds % 3600) // 60); s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:05.2f}"

    def _busy(self, on: bool, msg: str = ""):
        if on:
            self.progress.start(12)
            self.run_btn.state(["disabled"])
            self.preview_btn.state(["disabled"])
            if msg:
                self._log(msg)
        else:
            self.progress.stop()
            self.run_btn.state(["!disabled"])
            self.preview_btn.state(["!disabled"])

    def _log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def main() -> int:
    root = tk.Tk()
    root.title("chapterize — audiobook chapterizer")
    root.geometry("760x640")
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
