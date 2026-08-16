#!/usr/bin/env python3
from __future__ import annotations

"""
Nasty Batch Pit — Tkinter GUI for batch image/video processing.

Cosmic / lewd night-ops skin (deep purple + hot magenta), matching
Nasty Command Pit / Nasty Session Pit. Functionality is unchanged:
reuses core logic from image_batch.py.

Launch:
    python image_batch_gui.py
or
    python image_batch.py --gui

Requirements: only stdlib (tkinter) + the same Pillow/tqdm that the CLI needs.
No new dependencies.
"""

import queue
import threading
from pathlib import Path
from typing import Optional

# Import the battle-tested core functions (with log_func support)
try:
    from image_batch import (
        strip_metadata, batch_rename,
        compress_image, compress_video,
        resize_media, trim_video, mute_video,
        ffmpeg_available
    )
except ImportError:
    # Allow running the GUI directly even if PYTHONPATH is weird
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from image_batch import (
        strip_metadata, batch_rename,
        compress_image, compress_video,
        resize_media, trim_video, mute_video,
        ffmpeg_available
    )

# Tkinter is stdlib but not always installed (e.g. minimal servers).
# We import it lazily so "import image_batch_gui" doesn't blow up in headless envs.
TK_AVAILABLE = False
tk = None
ttk = None
filedialog = None
scrolledtext = None

# Cosmic / lewd night-ops palette (aligned with Nasty Command Pit)
# Text colors tuned for readable contrast on dark purple panels (not light seams).
THEME = {
    "bg": "#0d0514",
    "bg_deep": "#08030e",
    "bg_panel": "#12081c",
    "bg_hover": "#1f0f2e",
    "bg_active": "#2a143c",
    "bg_input": "#1a0a24",
    "bg_disabled": "#10061a",
    "border": "#3a1848",
    "text": "#f8eef6",
    "text_muted": "#d4b8d0",  # brighter muted — readable on dark panels
    "text_disabled": "#9a7a96",
    "accent": "#ff2d95",
    "accent_hover": "#ff5aad",
    "sub": "#d4b8ff",
    "success": "#5dffb0",
    "error": "#ff6b9d",
    "log_bg": "#0a0410",
    "log_fg": "#f8eef6",
    "log_insert": "#ff2d95",
}

# Default window: wide enough that Input/Output "Browse..." stay fully visible
DEFAULT_GEOMETRY = "960x720"
MIN_WIDTH = 820
MIN_HEIGHT = 560

APP_TITLE = "Nasty Batch Pit"
ICON_PATH = Path(__file__).parent / "assets" / "nasty-batch-pit.png"


def _ensure_tk():
    global TK_AVAILABLE, tk, ttk, filedialog, scrolledtext
    if TK_AVAILABLE:
        return True
    try:
        import tkinter as _tk
        from tkinter import ttk as _ttk, filedialog as _fd, scrolledtext as _st
        tk = _tk
        ttk = _ttk
        filedialog = _fd
        scrolledtext = _st
        TK_AVAILABLE = True
        return True
    except ImportError:
        return False


class ImageBatchGUI:
    def __init__(self, root: "tk.Tk"):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(DEFAULT_GEOMETRY)
        self.root.minsize(MIN_WIDTH, MIN_HEIGHT)
        self.root.configure(bg=THEME["bg"])

        self._set_window_icon()
        self._apply_theme()

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker_thread: Optional[threading.Thread] = None

        self._build_ui()
        self._poll_log_queue()

    def _set_window_icon(self):
        if not ICON_PATH.is_file():
            return
        try:
            icon = tk.PhotoImage(file=str(ICON_PATH))
            self.root.iconphoto(True, icon)
            # keep a reference so Tk doesn't GC the image
            self._icon_image = icon
        except Exception:
            pass

    def _apply_theme(self):
        """Apply deep-purple + magenta ttk theme (clam base)."""
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        bg = THEME["bg"]
        panel = THEME["bg_panel"]
        text = THEME["text"]
        muted = THEME["text_muted"]
        disabled_fg = THEME["text_disabled"]
        disabled_bg = THEME["bg_disabled"]
        accent = THEME["accent"]
        accent_h = THEME["accent_hover"]
        border = THEME["border"]
        inp = THEME["bg_input"]
        hover = THEME["bg_hover"]
        active = THEME["bg_active"]
        sub = THEME["sub"]

        style.configure(".", background=bg, foreground=text, fieldbackground=inp,
                        bordercolor=border, troughcolor=panel, focuscolor=accent,
                        selectbackground=accent, selectforeground="#12081c")
        # Default frames/labels match panel so controls inside LabelFrames don't flash light seams
        style.configure("TFrame", background=panel)
        style.configure("Content.TFrame", background=bg)
        style.configure("Panel.TFrame", background=panel)
        style.configure("TLabel", background=panel, foreground=text)
        style.map("TLabel",
                  background=[("disabled", panel)],
                  foreground=[("disabled", disabled_fg)])
        style.configure("Muted.TLabel", background=panel, foreground=muted)
        style.configure("Header.TLabel", background=bg, foreground=accent,
                        font=("Segoe UI", 16, "bold"))
        style.configure("Subhead.TLabel", background=bg, foreground=sub,
                        font=("Segoe UI", 9))
        style.configure("Status.TLabel", background=THEME["bg_deep"], foreground=muted,
                        relief="flat", padding=(8, 4))

        style.configure("TLabelframe", background=panel, foreground=accent,
                        bordercolor=border, relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=panel, foreground=accent,
                        font=("Segoe UI", 9, "bold"))

        style.configure("TEntry", fieldbackground=inp, foreground=text,
                        insertcolor=accent, bordercolor=border, lightcolor=border,
                        darkcolor=border, padding=4)
        style.map("TEntry",
                  fieldbackground=[("disabled", disabled_bg), ("readonly", inp),
                                   ("!disabled", inp)],
                  foreground=[("disabled", disabled_fg), ("!disabled", text)],
                  bordercolor=[("disabled", border), ("focus", accent)])

        style.configure("TSpinbox", fieldbackground=inp, foreground=text,
                        insertcolor=accent, bordercolor=border, arrowcolor=accent,
                        background=panel, lightcolor=border, darkcolor=border)
        style.map("TSpinbox",
                  fieldbackground=[("disabled", disabled_bg), ("!disabled", inp)],
                  foreground=[("disabled", disabled_fg), ("!disabled", text)],
                  arrowcolor=[("disabled", disabled_fg), ("!disabled", accent)])

        style.configure("TCombobox", fieldbackground=inp, foreground=text,
                        background=panel, bordercolor=border, arrowcolor=accent)
        style.map("TCombobox",
                  fieldbackground=[("readonly", inp), ("disabled", disabled_bg),
                                   ("!disabled", inp)],
                  foreground=[("disabled", disabled_fg), ("readonly", text),
                              ("!disabled", text)],
                  arrowcolor=[("disabled", disabled_fg), ("!disabled", accent)])

        style.configure("TButton", background=hover, foreground=text,
                        bordercolor=border, lightcolor=border, darkcolor=border,
                        focuscolor=accent, padding=(10, 5))
        style.map("TButton",
                  background=[("active", active), ("disabled", disabled_bg),
                              ("pressed", accent)],
                  foreground=[("disabled", disabled_fg), ("pressed", text),
                              ("!disabled", text)])

        style.configure("Accent.TButton", background=accent, foreground="#12081c",
                        bordercolor=accent, lightcolor=accent, darkcolor=accent,
                        focuscolor=accent_h, font=("Segoe UI", 10, "bold"),
                        padding=(12, 8))
        style.map("Accent.TButton",
                  background=[("active", accent_h), ("disabled", "#4a2040"),
                              ("pressed", "#e91e8c")],
                  foreground=[("disabled", disabled_fg), ("!disabled", "#12081c")])

        style.configure("TRadiobutton", background=panel, foreground=text,
                        indicatorcolor=inp, focuscolor=accent)
        style.map("TRadiobutton",
                  background=[("active", hover), ("disabled", panel)],
                  foreground=[("disabled", disabled_fg), ("!disabled", text)],
                  indicatorcolor=[("selected", accent), ("!selected", inp),
                                  ("disabled", disabled_bg)])

        style.configure("TCheckbutton", background=panel, foreground=text,
                        indicatorcolor=inp, focuscolor=accent)
        style.map("TCheckbutton",
                  background=[("active", hover), ("disabled", panel)],
                  foreground=[("disabled", disabled_fg), ("!disabled", text)],
                  indicatorcolor=[("selected", accent), ("!selected", inp),
                                  ("disabled", disabled_bg)])

        style.configure("Vertical.TScrollbar", background=panel, troughcolor=bg,
                        bordercolor=border, arrowcolor=accent, lightcolor=panel,
                        darkcolor=panel)
        style.configure("Horizontal.TScrollbar", background=panel, troughcolor=bg,
                        bordercolor=border, arrowcolor=accent, lightcolor=panel,
                        darkcolor=panel)
        style.map("Vertical.TScrollbar",
                  background=[("active", active), ("disabled", panel)],
                  arrowcolor=[("disabled", disabled_fg)])
        style.map("Horizontal.TScrollbar",
                  background=[("active", active), ("disabled", panel)],
                  arrowcolor=[("disabled", disabled_fg)])

        # Force dark Tk defaults so no light system seams under labels / entries
        try:
            self.root.option_add("*Background", bg)
            self.root.option_add("*Foreground", text)
            self.root.option_add("*selectBackground", accent)
            self.root.option_add("*selectForeground", "#12081c")
            self.root.option_add("*Entry.Background", inp)
            self.root.option_add("*Entry.Foreground", text)
            self.root.option_add("*Text.Background", THEME["log_bg"])
            self.root.option_add("*Text.Foreground", THEME["log_fg"])
            self.root.option_add("*TCombobox*Listbox.background", inp)
            self.root.option_add("*TCombobox*Listbox.foreground", text)
            self.root.option_add("*TCombobox*Listbox.selectBackground", accent)
            self.root.option_add("*TCombobox*Listbox.selectForeground", "#12081c")
        except Exception:
            pass

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # Simple scroll wrapper (vertical + horizontal)
        canvas = tk.Canvas(self.root, highlightthickness=0, bg=THEME["bg"],
                           borderwidth=0)
        vbar = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        hbar = ttk.Scrollbar(self.root, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)

        content = ttk.Frame(canvas, style="Content.TFrame")

        # === Header ===
        header = ttk.Frame(content, style="Content.TFrame")
        header.pack(fill="x", padx=8, pady=(10, 2))
        ttk.Label(header, text="✦  Nasty Batch Pit", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Cosmic batch forge · strip · rename · compress · resize · trim · mute",
            style="Subhead.TLabel",
        ).pack(anchor="w", pady=(0, 4))

        # === Input directory ===
        # Pack Browse first (side=right) so expand=True Entry cannot hide it.
        frm = ttk.LabelFrame(content, text="Input Folder")
        frm.pack(fill="x", **pad)

        self.input_var = tk.StringVar()
        ttk.Button(frm, text="Browse...", command=self._browse_input).pack(
            side="right", padx=(4, 8), pady=6
        )
        ttk.Entry(frm, textvariable=self.input_var).pack(
            side="left", fill="x", expand=True, padx=(8, 4), pady=6
        )

        # === Output directory (mainly for strip_meta) ===
        frm = ttk.LabelFrame(content, text="Output Folder (recommended for strip / when using output)")
        frm.pack(fill="x", **pad)

        self.output_var = tk.StringVar()
        ttk.Button(frm, text="Browse...", command=self._browse_output).pack(
            side="right", padx=(4, 8), pady=6
        )
        ttk.Entry(frm, textvariable=self.output_var).pack(
            side="left", fill="x", expand=True, padx=(8, 4), pady=6
        )

        # === Action selection ===
        frm = ttk.LabelFrame(content, text="Action")
        frm.pack(fill="x", **pad)

        self.action_var = tk.StringVar(value="strip_meta")
        ttk.Radiobutton(
            frm, text="Strip Metadata", variable=self.action_var,
            value="strip_meta", command=self._on_action_change
        ).pack(anchor="w", padx=8)
        ttk.Radiobutton(
            frm, text="Rename", variable=self.action_var,
            value="rename", command=self._on_action_change
        ).pack(anchor="w", padx=8)
        ttk.Radiobutton(
            frm, text="Compress Image (to WebP, quality or target size)",
            variable=self.action_var, value="compress", command=self._on_action_change
        ).pack(anchor="w", padx=8)
        ttk.Radiobutton(
            frm, text="Compress Video (CRF + preset)", variable=self.action_var,
            value="compress-video", command=self._on_action_change
        ).pack(anchor="w", padx=8)
        ttk.Radiobutton(
            frm, text="Resize (images/videos, width/height or scale)",
            variable=self.action_var, value="resize", command=self._on_action_change
        ).pack(anchor="w", padx=8)
        ttk.Radiobutton(
            frm, text="Trim Video (start / end time)", variable=self.action_var,
            value="trim", command=self._on_action_change
        ).pack(anchor="w", padx=8)
        ttk.Radiobutton(
            frm, text="Mute Video Audio", variable=self.action_var,
            value="mute", command=self._on_action_change
        ).pack(anchor="w", padx=8)

        # === Rename options ===
        self.rename_frame = ttk.LabelFrame(content, text="Rename Options")
        self.rename_frame.pack(fill="x", **pad)

        row = ttk.Frame(self.rename_frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Pattern (substring in filename)").pack(side="left")
        self.pattern_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.pattern_var, width=25).pack(side="left", padx=6)

        row = ttk.Frame(self.rename_frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="New Prefix").pack(side="left")
        self.prefix_var = tk.StringVar(value="img_")
        ttk.Entry(row, textvariable=self.prefix_var, width=15).pack(side="left", padx=6)

        row = ttk.Frame(self.rename_frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Start Number").pack(side="left")
        self.start_var = tk.IntVar(value=1)
        ttk.Spinbox(row, from_=1, to=9999, textvariable=self.start_var, width=6).pack(
            side="left", padx=6
        )

        # === Parameters (for new features) ===
        self.params_frame = ttk.LabelFrame(
            content, text="Parameters (compression, resize, trim)"
        )
        self.params_frame.pack(fill="x", **pad)

        # Quality / CRF
        row = ttk.Frame(self.params_frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Quality (WebP 0-100)").pack(side="left")
        self.quality_var = tk.IntVar(value=78)
        ttk.Spinbox(row, from_=1, to=100, textvariable=self.quality_var, width=6).pack(
            side="left", padx=4
        )

        ttk.Label(row, text=" Target size (bytes)").pack(side="left", padx=10)
        self.target_size_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.target_size_var, width=10).pack(
            side="left", padx=2
        )

        ttk.Label(row, text="  CRF (video 0-51)").pack(side="left", padx=10)
        self.crf_var = tk.IntVar(value=28)
        ttk.Spinbox(row, from_=0, to=51, textvariable=self.crf_var, width=6).pack(
            side="left", padx=4
        )

        ttk.Label(row, text="  Preset").pack(side="left", padx=10)
        self.preset_var = tk.StringVar(value="medium")
        ttk.Combobox(
            row, textvariable=self.preset_var,
            values=["medium", "slow", "slower", "fast"], width=8, state="readonly"
        ).pack(side="left")

        # Resize
        row = ttk.Frame(self.params_frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Width").pack(side="left")
        self.width_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.width_var, width=8).pack(side="left", padx=4)
        ttk.Label(row, text="Height").pack(side="left")
        self.height_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.height_var, width=8).pack(side="left", padx=4)
        ttk.Label(row, text=" or Scale (0.5=50%)").pack(side="left", padx=10)
        self.scale_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.scale_var, width=6).pack(side="left", padx=4)

        # Video trim / mute
        row = ttk.Frame(self.params_frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Trim Start (e.g. 00:01:30)").pack(side="left")
        self.trim_start_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.trim_start_var, width=10).pack(
            side="left", padx=4
        )
        ttk.Label(row, text="End").pack(side="left")
        self.trim_end_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.trim_end_var, width=10).pack(side="left", padx=4)
        self.mute_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Mute Audio", variable=self.mute_var).pack(
            side="left", padx=10
        )

        # === Options ===
        frm = ttk.LabelFrame(content, text="Options")
        frm.pack(fill="x", **pad)

        self.recursive_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frm, text="Recursive (include subfolders)", variable=self.recursive_var
        ).pack(anchor="w", padx=8)

        self.dryrun_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frm, text="Dry Run (preview only, no changes)", variable=self.dryrun_var
        ).pack(anchor="w", padx=8)

        self.overwrite_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frm, text="Overwrite (in-place instead of _suffix)", variable=self.overwrite_var
        ).pack(anchor="w", padx=8)

        row = ttk.Frame(frm)
        row.pack(anchor="w", padx=8)
        ttk.Label(row, text="Delete extensions after success (e.g. jpg,jpeg):").pack(
            side="left"
        )
        self.delete_ext_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.delete_ext_var, width=15).pack(side="left")

        # === Run button ===
        self.run_btn = ttk.Button(
            content, text="▶  Execute / Run", style="Accent.TButton",
            command=self._on_run_clicked
        )
        self.run_btn.pack(fill="x", padx=8, pady=8, ipady=4)

        # === Log output ===
        frm = ttk.LabelFrame(content, text="Log / Progress")
        frm.pack(fill="both", expand=True, **pad)

        self.log_text = scrolledtext.ScrolledText(
            frm, height=18, wrap="word", state="disabled",
            bg=THEME["log_bg"], fg=THEME["log_fg"],
            insertbackground=THEME["log_insert"],
            selectbackground=THEME["accent"],
            selectforeground="#12081c",
            relief="flat", borderwidth=0,
            font=("Consolas", 9),
        )
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

        # Status bar
        self.status_var = tk.StringVar(value="Ready. Pick a folder and hit Execute.")
        ttk.Label(
            content, textvariable=self.status_var, style="Status.TLabel", anchor="w"
        ).pack(fill="x", padx=4, pady=2)

        # Place content into canvas; keep content at least as wide as the viewport
        # so Browse rows don't require horizontal scroll at default size.
        content_win = canvas.create_window((0, 0), window=content, anchor="nw")

        def _update_scrollregion(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _stretch_content_width(event):
            # Match content width to canvas so rows use full window (Browse stays visible)
            canvas.itemconfigure(content_win, width=max(event.width, MIN_WIDTH - 40))
            _update_scrollregion()

        content.bind("<Configure>", _update_scrollregion)
        canvas.bind("<Configure>", _stretch_content_width)

        # Mouse wheel support
        def _on_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_wheel)
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))

        # Horizontal scroll with Shift + wheel
        def _on_shift_wheel(event):
            canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<Shift-MouseWheel>", _on_shift_wheel)
        canvas.bind_all("<Shift-Button-4>", lambda e: canvas.xview_scroll(-1, "units"))
        canvas.bind_all("<Shift-Button-5>", lambda e: canvas.xview_scroll(1, "units"))

        hbar.pack(side="bottom", fill="x")
        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        # Initial state
        self._on_action_change()

        # Tips
        self._log("=== Nasty Batch Pit ===")
        self._log("Supported images: JPG, JPEG, JFIF, PNG, WEBP, BMP, TIFF, GIF")
        self._log(
            "Tip: Browse stays visible at default width. Use scrollbars if you shrink a lot. "
            "Start with Dry Run enabled!"
        )
        self._log(
            "Features: compress (quality or target size), compress-video (CRF+preset), "
            "resize, trim, mute are all available."
        )
        self._log("Horizontal scroll: Shift + mouse wheel")
        self._log(
            "Note: Video actions with same input/output dir + Overwrite will show a "
            "warning and skip to prevent data loss."
        )

    def _browse_input(self):
        folder = filedialog.askdirectory(title="Select input folder with images")
        if folder:
            self.input_var.set(folder)

    def _browse_output(self):
        folder = filedialog.askdirectory(title="Select output folder (for cleaned images)")
        if folder:
            self.output_var.set(folder)

    def _on_action_change(self):
        action = self.action_var.get()
        # Enable/disable frames based on action
        rename_enabled = action == "rename"
        params_enabled = action in ("compress", "compress-video", "resize", "trim", "mute")

        for child in self.rename_frame.winfo_children():
            self._set_state_recursive(child, "normal" if rename_enabled else "disabled")

        for child in self.params_frame.winfo_children():
            self._set_state_recursive(child, "normal" if params_enabled else "disabled")

    def _set_state_recursive(self, widget, state):
        try:
            widget.configure(state=state)
        except tk.TclError:
            pass
        for child in getattr(widget, "winfo_children", lambda: [])():
            self._set_state_recursive(child, state)

    def _log(self, message: str):
        """Thread-safe log from worker thread."""
        self.log_queue.put(message)

    def _poll_log_queue(self):
        """Pull messages from the worker and display them in the Text widget."""
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        # Schedule next poll
        self.root.after(80, self._poll_log_queue)

    def _set_running(self, running: bool):
        if running:
            self.run_btn.config(state="disabled", text="Working...")
            self.status_var.set("Processing... do not close the window.")
        else:
            self.run_btn.config(state="normal", text="▶  Execute / Run")
            self.status_var.set("Done. Check the log above.")

    def _on_run_clicked(self):
        input_dir = self.input_var.get().strip()
        if not input_dir:
            self._log("ERROR: Input folder is required.")
            return

        action = self.action_var.get()
        recursive = self.recursive_var.get()
        dry_run = self.dryrun_var.get()
        output_dir = self.output_var.get().strip() or None

        # Clear previous log
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        self._set_running(True)

        if action == "strip_meta":
            self.worker_thread = threading.Thread(
                target=self._run_strip,
                args=(input_dir, output_dir, recursive, dry_run),
                daemon=True
            )
            self.worker_thread.start()

        elif action == "rename":
            pattern = self.pattern_var.get().strip()
            prefix = self.prefix_var.get().strip()
            start_num = self.start_var.get()
            if not pattern or not prefix:
                self._log("ERROR: For rename fill Pattern and New Prefix.")
                self._set_running(False)
                return
            self.worker_thread = threading.Thread(
                target=self._run_rename,
                args=(input_dir, pattern, prefix, start_num, recursive, dry_run),
                daemon=True
            )
            self.worker_thread.start()

        elif action == "compress":
            quality = self.quality_var.get()
            ts_str = self.target_size_var.get().strip()
            target_size = int(ts_str) if ts_str.isdigit() else None
            overwrite = self.overwrite_var.get()
            delete_exts = [
                e.strip().lower()
                for e in self.delete_ext_var.get().split(",")
                if e.strip()
            ] if self.delete_ext_var.get().strip() else []
            self.worker_thread = threading.Thread(
                target=self._run_compress_image,
                args=(
                    input_dir, quality, target_size, output_dir, recursive, dry_run,
                    overwrite, delete_exts
                ),
                daemon=True
            )
            self.worker_thread.start()

        elif action == "compress-video":
            crf = self.crf_var.get()
            preset = self.preset_var.get()
            mute = self.mute_var.get()
            overwrite = self.overwrite_var.get()
            delete_exts = [
                e.strip().lower()
                for e in self.delete_ext_var.get().split(",")
                if e.strip()
            ] if self.delete_ext_var.get().strip() else []
            if not ffmpeg_available():
                self._log("WARNING: ffmpeg not detected - video features may fail.")
            # VIDEO SAFETY: same input/output (incl. no-output=in-place) + overwrite is deadly
            eff_same = (not output_dir) or (
                Path(output_dir).resolve() == Path(input_dir).resolve()
            )
            if eff_same and overwrite and not dry_run:
                self._log(
                    "*** WARNING: Input==Output (or in-place) + Overwrite for VIDEO COMPRESS ***"
                )
                self._log(
                    "Original video files would be overwritten. "
                    "FILE LOSS PREVENTION FIRST -> SKIPPING."
                )
                self._log(
                    "Tip: set a different Output Folder, or uncheck 'Overwrite' "
                    "(creates _compressed.mp4 safely)."
                )
                self._set_running(False)
                return
            if eff_same and overwrite:
                self._log("[DRY or safe] Same dir + overwrite noted (sim only).")
            self.worker_thread = threading.Thread(
                target=self._run_compress_video,
                args=(
                    input_dir, crf, preset, output_dir, recursive, dry_run, mute,
                    overwrite, delete_exts
                ),
                daemon=True
            )
            self.worker_thread.start()

        elif action == "resize":
            w = int(self.width_var.get()) if self.width_var.get().strip().isdigit() else None
            h = int(self.height_var.get()) if self.height_var.get().strip().isdigit() else None
            sc = float(self.scale_var.get()) if self.scale_var.get().strip() else None
            overwrite = self.overwrite_var.get()
            delete_exts = [
                e.strip().lower()
                for e in self.delete_ext_var.get().split(",")
                if e.strip()
            ] if self.delete_ext_var.get().strip() else []
            # VIDEO SAFETY (resize affects video too)
            eff_same = (not output_dir) or (
                Path(output_dir).resolve() == Path(input_dir).resolve()
            )
            if eff_same and overwrite and not dry_run:
                self._log(
                    "*** WARNING: Same-dir (or in-place) + Overwrite for RESIZE on videos ***"
                )
                self._log(
                    "SKIPPING to avoid overwriting originals. "
                    "Prefer separate output or no overwrite."
                )
                self._set_running(False)
                return
            if eff_same and overwrite:
                self._log("[note] dry-run: same-dir overwrite resize sim.")
            self.worker_thread = threading.Thread(
                target=self._run_resize,
                args=(
                    input_dir, w, h, sc, output_dir, recursive, dry_run,
                    overwrite, delete_exts
                ),
                daemon=True
            )
            self.worker_thread.start()

        elif action == "trim":
            start = self.trim_start_var.get().strip() or None
            end = self.trim_end_var.get().strip() or None
            if not start and not end:
                self._log("ERROR: Provide Start or End for trim.")
                self._set_running(False)
                return
            overwrite = self.overwrite_var.get()
            delete_exts = [
                e.strip().lower()
                for e in self.delete_ext_var.get().split(",")
                if e.strip()
            ] if self.delete_ext_var.get().strip() else []
            eff_same = (not output_dir) or (
                Path(output_dir).resolve() == Path(input_dir).resolve()
            )
            if eff_same and overwrite and not dry_run:
                self._log(
                    "*** WARNING: Same dir + overwrite for VIDEO TRIM - "
                    "data loss possible! SKIPPING."
                )
                self._set_running(False)
                return
            if eff_same and overwrite:
                self._log("[dry] trim same-dir overwrite sim only.")
            self.worker_thread = threading.Thread(
                target=self._run_trim,
                args=(
                    input_dir, start, end, output_dir, recursive, dry_run,
                    overwrite, delete_exts
                ),
                daemon=True
            )
            self.worker_thread.start()

        elif action == "mute":
            overwrite = self.overwrite_var.get()
            delete_exts = [
                e.strip().lower()
                for e in self.delete_ext_var.get().split(",")
                if e.strip()
            ] if self.delete_ext_var.get().strip() else []
            eff_same = (not output_dir) or (
                Path(output_dir).resolve() == Path(input_dir).resolve()
            )
            if eff_same and overwrite and not dry_run:
                self._log(
                    "*** WARNING: Same dir + overwrite for VIDEO MUTE - "
                    "original would be replaced. SKIPPING for safety."
                )
                self._set_running(False)
                return
            if eff_same and overwrite:
                self._log("[dry] mute same-dir+overwrite sim.")
            self.worker_thread = threading.Thread(
                target=self._run_mute,
                args=(input_dir, output_dir, recursive, dry_run, overwrite, delete_exts),
                daemon=True
            )
            self.worker_thread.start()

    def _run_strip(self, input_dir: str, output_dir: Optional[str], recursive: bool, dry_run: bool):
        try:
            processed, errors = strip_metadata(
                input_dir,
                output_dir=output_dir,
                recursive=recursive,
                dry_run=dry_run,
                log_func=self._log
            )
            self._log("")
            self._log("=== strip_meta finished ===")
            self._log(f"Successfully processed: {processed}")
            self._log(f"Errors / skipped:       {errors}")
            if dry_run:
                self._log("DRY RUN - no files were modified.")
        except Exception as e:
            self._log(f"FATAL ERROR: {e}")
        finally:
            self.root.after(0, lambda: self._set_running(False))

    def _run_rename(self, input_dir: str, pattern: str, prefix: str, start_num: int, recursive: bool, dry_run: bool):
        try:
            processed, skipped = batch_rename(
                input_dir,
                pattern=pattern,
                new_prefix=prefix,
                start_num=start_num,
                recursive=recursive,
                dry_run=dry_run,
                log_func=self._log
            )
            self._log("")
            self._log("=== rename finished ===")
            self._log(f"Successfully renamed: {processed}")
            self._log(f"Skipped / errors:     {skipped}")
            if dry_run:
                self._log("DRY RUN - no files were modified.")
        except Exception as e:
            self._log(f"FATAL ERROR: {e}")
        finally:
            self.root.after(0, lambda: self._set_running(False))

    def _run_compress_image(self, input_dir: str, quality: int, target_size: int | None, output_dir: str | None, recursive: bool, dry_run: bool, overwrite: bool = False, delete_exts: list = None):
        try:
            processed, errors = compress_image(
                input_dir, quality=quality, target_size=target_size,
                output_dir=output_dir, recursive=recursive, dry_run=dry_run,
                overwrite=overwrite, delete_exts=delete_exts, log_func=self._log
            )
            self._log("")
            ts = f" target={target_size}" if target_size else ""
            self._log(f"=== compress-image (quality={quality}{ts}) finished ===")
            self._log(f"Processed: {processed}  Errors: {errors}")
        except Exception as e:
            self._log(f"FATAL ERROR: {e}")
        finally:
            self.root.after(0, lambda: self._set_running(False))

    def _run_compress_video(self, input_dir: str, crf: int, preset: str, output_dir: str | None, recursive: bool, dry_run: bool, mute: bool, overwrite: bool = False, delete_exts: list = None):
        try:
            processed, errors = compress_video(
                input_dir, crf=crf, preset=preset, output_dir=output_dir,
                recursive=recursive, dry_run=dry_run, mute=mute,
                overwrite=overwrite, delete_exts=delete_exts, log_func=self._log
            )
            self._log("")
            self._log(f"=== compress-video (crf={crf}, preset={preset}) finished ===")
            self._log(f"Processed: {processed}  Errors: {errors}")
        except Exception as e:
            self._log(f"FATAL ERROR: {e}")
        finally:
            self.root.after(0, lambda: self._set_running(False))

    def _run_resize(self, input_dir: str, width: int | None, height: int | None, scale: float | None, output_dir: str | None, recursive: bool, dry_run: bool, overwrite: bool = False, delete_exts: list = None):
        try:
            processed, errors = resize_media(
                input_dir, width=width, height=height, scale=scale,
                output_dir=output_dir, recursive=recursive, dry_run=dry_run,
                overwrite=overwrite, delete_exts=delete_exts, log_func=self._log
            )
            self._log("")
            self._log("=== resize finished ===")
            self._log(f"Processed: {processed}  Errors: {errors}")
        except Exception as e:
            self._log(f"FATAL ERROR: {e}")
        finally:
            self.root.after(0, lambda: self._set_running(False))

    def _run_trim(self, input_dir: str, start: str | None, end: str | None, output_dir: str | None, recursive: bool, dry_run: bool, overwrite: bool = False, delete_exts: list = None):
        try:
            processed, errors = trim_video(
                input_dir, start=start, end=end, output_dir=output_dir,
                recursive=recursive, dry_run=dry_run, overwrite=overwrite,
                delete_exts=delete_exts, log_func=self._log
            )
            self._log("")
            self._log("=== trim finished ===")
            self._log(f"Processed: {processed}  Errors: {errors}")
        except Exception as e:
            self._log(f"FATAL ERROR: {e}")
        finally:
            self.root.after(0, lambda: self._set_running(False))

    def _run_mute(self, input_dir: str, output_dir: str | None, recursive: bool, dry_run: bool, overwrite: bool = False, delete_exts: list = None):
        try:
            processed, errors = mute_video(
                input_dir, output_dir=output_dir, recursive=recursive,
                dry_run=dry_run, overwrite=overwrite, delete_exts=delete_exts,
                log_func=self._log
            )
            self._log("")
            self._log("=== mute finished ===")
            self._log(f"Processed: {processed}  Errors: {errors}")
        except Exception as e:
            self._log(f"FATAL ERROR: {e}")
        finally:
            self.root.after(0, lambda: self._set_running(False))


def run_gui():
    if not _ensure_tk():
        print("ERROR: Tkinter is not available in this Python installation.")
        print("On Ubuntu/Debian: sudo apt install python3-tk")
        print("On Fedora: sudo dnf install python3-tkinter")
        print("Then re-run: python image_batch_gui.py or python image_batch.py --gui")
        return

    # Now safe to use the names
    root = tk.Tk()
    app = ImageBatchGUI(root)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
