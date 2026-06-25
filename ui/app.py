"""
SpeechMonitor – main application window.

Compact mode  : small always-on-top overlay (300 × 160 px)
Dashboard mode: full window with charts, transcript and settings
"""

import math
import queue
import threading
import time
import tkinter as tk
from collections import deque
from typing import Optional

import customtkinter as ctk

from core.audio_capture import AudioCapture
from core.config import Config
from core.speech_processor import SpeechProcessor, SpeechResult
from utils.notifier import Notifier

# ── colour palette ────────────────────────────────────────────────────── #
BG        = "#0f0f1a"
CARD      = "#1a1a2e"
CARD2     = "#16213e"
BORDER    = "#2a2a4a"
TEXT      = "#e0e0ff"
SUBTEXT   = "#7070a0"
GREEN     = "#4caf50"
YELLOW    = "#ffc107"
ORANGE    = "#ff9800"
RED_C     = "#f44336"
BLUE      = "#4fc3f7"
ACCENT    = "#7c4dff"

COMPACT_W, COMPACT_H   = 320, 165
DASH_W,    DASH_H      = 960, 620

CHART_POINTS = 60   # number of WPM history points to show


def wpm_color(wpm: int, cfg: Config) -> str:
    if wpm == 0:           return SUBTEXT
    if wpm < cfg.get("wpm_slow"):      return BLUE
    if wpm < cfg.get("wpm_good_max"):  return GREEN
    if wpm < cfg.get("wpm_fast"):      return YELLOW
    if wpm < cfg.get("wpm_very_fast"): return ORANGE
    return RED_C


def wpm_label(wpm: int, cfg: Config) -> str:
    if wpm == 0:                       return "Listening…"
    if wpm < cfg.get("wpm_slow"):      return "Too slow"
    if wpm < cfg.get("wpm_good_max"):  return "Great pace!"
    if wpm < cfg.get("wpm_fast"):      return "Slightly fast"
    if wpm < cfg.get("wpm_very_fast"): return "Speaking fast!"
    return "Way too fast!"


def clarity_color(score: float) -> str:
    if score >= 80: return GREEN
    if score >= 60: return YELLOW
    return RED_C


# ═══════════════════════════════════════════════════════════════════════ #
#  Gauge canvas widget                                                    #
# ═══════════════════════════════════════════════════════════════════════ #

class GaugeWidget(tk.Canvas):
    MAX_WPM = 250

    def __init__(self, parent, size: int = 180, **kw):
        super().__init__(parent, width=size, height=size,
                         bg=CARD, highlightthickness=0, **kw)
        self.size = size
        self.cx = size // 2
        self.cy = size // 2
        self.r  = size // 2 - 18
        self._wpm = 0
        self._cfg: Optional[Config] = None
        self._draw()

    def configure_config(self, cfg: Config):
        self._cfg = cfg

    def set_wpm(self, wpm: int):
        self._wpm = int(wpm)
        self._draw()

    def _draw(self):
        self.delete("all")
        cx, cy, r = self.cx, self.cy, self.r

        # ── background arc ─────────────────────────────────────────────
        self.create_arc(cx-r, cy-r, cx+r, cy+r,
                        start=225, extent=-270,
                        outline="#2a2a4a", width=14, style=tk.ARC)

        # ── coloured fill ──────────────────────────────────────────────
        if self._wpm > 0:
            ratio   = min(1.0, self._wpm / self.MAX_WPM)
            extent  = -270 * ratio
            color   = wpm_color(self._wpm, self._cfg) if self._cfg else GREEN
            self.create_arc(cx-r, cy-r, cx+r, cy+r,
                            start=225, extent=extent,
                            outline=color, width=14, style=tk.ARC)

        # ── tick marks (0 / 100 / 150 / 190 / 250) ────────────────────
        ticks = [(0, "0"), (100, "100"), (150, "150"), (190, "190"), (250, "250")]
        for val, lbl in ticks:
            frac  = val / self.MAX_WPM
            deg   = 225 - 270 * frac        # position along the arc
            rad   = math.radians(deg)
            tx    = cx + (r + 14) * math.cos(rad)
            ty    = cy - (r + 14) * math.sin(rad)
            self.create_text(tx, ty, text=lbl, fill=SUBTEXT,
                             font=("Helvetica", 7))

        # ── centre number ──────────────────────────────────────────────
        color = wpm_color(self._wpm, self._cfg) if self._cfg else TEXT
        self.create_text(cx, cy - 12, text=str(self._wpm),
                         fill=color, font=("Helvetica", 34, "bold"))
        self.create_text(cx, cy + 16, text="WPM",
                         fill=SUBTEXT, font=("Helvetica", 11))


# ═══════════════════════════════════════════════════════════════════════ #
#  WPM chart (drawn on Canvas)                                           #
# ═══════════════════════════════════════════════════════════════════════ #

class ChartWidget(tk.Canvas):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=CARD2, highlightthickness=0, **kw)
        self._history: deque = deque(maxlen=CHART_POINTS)
        self._cfg: Optional[Config] = None
        self.bind("<Configure>", lambda _: self._draw())

    def configure_config(self, cfg: Config):
        self._cfg = cfg

    def push(self, wpm: int):
        self._history.append(wpm)
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10:
            return

        pad = 30
        inner_w = w - pad * 2
        inner_h = h - pad * 2

        # ── grid lines ─────────────────────────────────────────────────
        for val in [0, 100, 150, 190, 250]:
            y = pad + inner_h - (val / 250) * inner_h
            self.create_line(pad, y, w - pad, y, fill=BORDER, dash=(4, 6))
            self.create_text(pad - 4, y, text=str(val), fill=SUBTEXT,
                             anchor="e", font=("Helvetica", 8))

        if not self._history:
            self.create_text(w // 2, h // 2, text="No data yet",
                             fill=SUBTEXT, font=("Helvetica", 12))
            return

        pts = list(self._history)
        step = inner_w / max(CHART_POINTS - 1, 1)

        # ── fill area ──────────────────────────────────────────────────
        poly = []
        for i, v in enumerate(pts):
            x = pad + i * step
            y = pad + inner_h - (min(v, 250) / 250) * inner_h
            poly.extend([x, y])
        if len(poly) >= 4:
            poly = [pad, pad + inner_h] + poly + [pad + (len(pts)-1)*step, pad + inner_h]
            self.create_polygon(poly, fill="#1a1040", outline="")

        # ── line ───────────────────────────────────────────────────────
        coords = []
        for i, v in enumerate(pts):
            x = pad + i * step
            y = pad + inner_h - (min(v, 250) / 250) * inner_h
            coords.extend([x, y])
        if len(coords) >= 4:
            self.create_line(coords, fill=ACCENT, width=2, smooth=True)

        # ── dots ───────────────────────────────────────────────────────
        for i, v in enumerate(pts):
            x = pad + i * step
            y = pad + inner_h - (min(v, 250) / 250) * inner_h
            col = wpm_color(v, self._cfg) if self._cfg else ACCENT
            self.create_oval(x-3, y-3, x+3, y+3, fill=col, outline="")

        # x-axis label
        self.create_text(w // 2, h - 8, text="← last 60 s →",
                         fill=SUBTEXT, font=("Helvetica", 8))


# ═══════════════════════════════════════════════════════════════════════ #
#  Main application                                                       #
# ═══════════════════════════════════════════════════════════════════════ #

class SpeechMonitorApp:
    def __init__(self, config: Config):
        self.cfg       = config
        self.notifier  = Notifier(cooldown_sec=config.get("notification_cooldown"))
        self._seg_q    : queue.Queue = queue.Queue()
        self._capture  : Optional[AudioCapture]   = None
        self._processor: Optional[SpeechProcessor] = None
        self._monitoring = False
        self._model_ready = False
        self._fast_since: Optional[float] = None
        self._low_clarity_since: Optional[float] = None

        # transcript history (text, wpm, clarity)
        self._transcript_log: list[tuple[str, int, float]] = []

        self._build_window()
        self._load_model_async()

    # ────────────────────────────────────────────────────────────────── #
    #  Window construction                                               #
    # ────────────────────────────────────────────────────────────────── #

    def _build_window(self):
        self.root = ctk.CTk()
        self.root.title("Speech Monitor")
        self.root.configure(fg_color=BG)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", self.cfg.get("always_on_top"))

        self._compact_mode = True
        self._set_compact_geometry()

        # ── outer container ────────────────────────────────────────────
        self._container = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        self._container.pack(fill="both", expand=True)

        self._build_compact_frame()
        self._build_dashboard_frame()

        self._show_compact()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Make window draggable via the title area in compact mode
        self._drag_x = 0
        self._drag_y = 0

    # ── compact frame ──────────────────────────────────────────────────

    def _build_compact_frame(self):
        self._cf = ctk.CTkFrame(self._container, fg_color=CARD,
                                corner_radius=14, border_width=1,
                                border_color=BORDER)

        # title bar
        tb = ctk.CTkFrame(self._cf, fg_color=CARD2, corner_radius=10,
                          height=28)
        tb.pack(fill="x", padx=6, pady=(6, 0))
        tb.pack_propagate(False)

        ctk.CTkLabel(tb, text="● Speech Monitor", text_color=ACCENT,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(
                     side="left", padx=10)

        self._c_expand_btn = ctk.CTkButton(
            tb, text="⛶", width=26, height=22, corner_radius=6,
            fg_color=BORDER, hover_color=ACCENT,
            command=self._switch_to_dashboard,
        )
        self._c_expand_btn.pack(side="right", padx=6)

        # drag binding
        tb.bind("<ButtonPress-1>",   self._drag_start)
        tb.bind("<B1-Motion>",       self._drag_motion)

        # ── body ───────────────────────────────────────────────────────
        body = ctk.CTkFrame(self._cf, fg_color=CARD, corner_radius=0)
        body.pack(fill="both", expand=True, padx=6, pady=4)

        # WPM big number
        left = ctk.CTkFrame(body, fg_color=CARD, corner_radius=0)
        left.pack(side="left", fill="y", padx=(4, 0))

        self._c_wpm_label = ctk.CTkLabel(
            left, text="0",
            text_color=SUBTEXT,
            font=ctk.CTkFont(size=52, weight="bold"),
        )
        self._c_wpm_label.pack(pady=(2, 0))
        ctk.CTkLabel(left, text="WPM", text_color=SUBTEXT,
                     font=ctk.CTkFont(size=11)).pack()

        # right column
        right = ctk.CTkFrame(body, fg_color=CARD, corner_radius=0)
        right.pack(side="left", fill="both", expand=True, padx=8)

        self._c_status_label = ctk.CTkLabel(
            right, text="Idle", text_color=SUBTEXT,
            font=ctk.CTkFont(size=13, weight="bold"), wraplength=160,
        )
        self._c_status_label.pack(anchor="w", pady=(8, 2))

        # clarity bar
        ctk.CTkLabel(right, text="Clarity", text_color=SUBTEXT,
                     font=ctk.CTkFont(size=10)).pack(anchor="w")
        self._c_clarity_bar = ctk.CTkProgressBar(right, height=8,
                                                  corner_radius=4,
                                                  progress_color=GREEN)
        self._c_clarity_bar.pack(fill="x", pady=(0, 4))
        self._c_clarity_bar.set(1.0)

        # start / stop button
        self._c_toggle_btn = ctk.CTkButton(
            right, text="▶  Start", height=30, corner_radius=8,
            fg_color=ACCENT, hover_color="#9c6dff",
            command=self._toggle_monitoring,
        )
        self._c_toggle_btn.pack(fill="x")

    # ── dashboard frame ────────────────────────────────────────────────

    def _build_dashboard_frame(self):
        self._df = ctk.CTkFrame(self._container, fg_color=BG, corner_radius=0)

        # title bar
        tb = ctk.CTkFrame(self._df, fg_color=CARD2, corner_radius=0, height=42)
        tb.pack(fill="x")
        tb.pack_propagate(False)

        ctk.CTkLabel(tb, text="  ● Speech Monitor",
                     text_color=ACCENT,
                     font=ctk.CTkFont(size=14, weight="bold")).pack(
                     side="left", padx=8)

        self._d_compact_btn = ctk.CTkButton(
            tb, text="⊟ Compact", width=90, height=28, corner_radius=6,
            fg_color=BORDER, hover_color=ACCENT,
            command=self._switch_to_compact,
        )
        self._d_compact_btn.pack(side="right", padx=10)

        self._d_toggle_btn = ctk.CTkButton(
            tb, text="▶  Start", width=90, height=28, corner_radius=6,
            fg_color=ACCENT, hover_color="#9c6dff",
            command=self._toggle_monitoring,
        )
        self._d_toggle_btn.pack(side="right", padx=4)

        # tab view
        self._tabs = ctk.CTkTabview(self._df, fg_color=CARD,
                                    segmented_button_fg_color=CARD2,
                                    segmented_button_selected_color=ACCENT,
                                    segmented_button_unselected_color=CARD2,
                                    segmented_button_selected_hover_color="#9c6dff",
                                    text_color=TEXT)
        self._tabs.pack(fill="both", expand=True, padx=10, pady=8)

        for name in ("Monitor", "Statistics", "Settings"):
            self._tabs.add(name)

        self._build_monitor_tab()
        self._build_stats_tab()
        self._build_settings_tab()

    # ── monitor tab ────────────────────────────────────────────────────

    def _build_monitor_tab(self):
        tab = self._tabs.tab("Monitor")
        tab.configure(fg_color=BG)

        # ── row 0: gauge  |  live stats  |  alert panel ───────────────
        top = ctk.CTkFrame(tab, fg_color=BG)
        top.pack(fill="x", padx=4, pady=(4, 0))

        # gauge card
        g_card = ctk.CTkFrame(top, fg_color=CARD, corner_radius=12,
                               border_width=1, border_color=BORDER)
        g_card.pack(side="left", padx=(0, 6), pady=4)

        self._gauge = GaugeWidget(g_card, size=185)
        self._gauge.configure_config(self.cfg)
        self._gauge.pack(padx=12, pady=10)

        # live stats card
        s_card = ctk.CTkFrame(top, fg_color=CARD, corner_radius=12,
                               border_width=1, border_color=BORDER)
        s_card.pack(side="left", fill="y", padx=(0, 6), pady=4)

        self._d_status = ctk.CTkLabel(
            s_card, text="Idle",
            text_color=SUBTEXT, font=ctk.CTkFont(size=15, weight="bold"),
        )
        self._d_status.pack(anchor="w", padx=16, pady=(14, 4))

        def stat_row(parent, label):
            f = ctk.CTkFrame(parent, fg_color=CARD2, corner_radius=8,
                             height=38)
            f.pack(fill="x", padx=10, pady=3)
            f.pack_propagate(False)
            ctk.CTkLabel(f, text=label, text_color=SUBTEXT,
                         font=ctk.CTkFont(size=11)).pack(side="left", padx=10)
            val = ctk.CTkLabel(f, text="—", text_color=TEXT,
                               font=ctk.CTkFont(size=13, weight="bold"))
            val.pack(side="right", padx=10)
            return val

        self._d_wpm_val     = stat_row(s_card, "Current WPM")
        self._d_clarity_val = stat_row(s_card, "Clarity")
        self._d_words_val   = stat_row(s_card, "Words (session)")
        self._d_avg_val     = stat_row(s_card, "Avg WPM")
        self._d_peak_val    = stat_row(s_card, "Peak WPM")

        # clarity bar
        cl_f = ctk.CTkFrame(s_card, fg_color=CARD2, corner_radius=8, height=44)
        cl_f.pack(fill="x", padx=10, pady=(3, 10))
        cl_f.pack_propagate(False)
        ctk.CTkLabel(cl_f, text="Clarity", text_color=SUBTEXT,
                     font=ctk.CTkFont(size=11)).pack(side="left", padx=10)
        self._d_clarity_bar = ctk.CTkProgressBar(cl_f, height=10,
                                                   corner_radius=4,
                                                   progress_color=GREEN,
                                                   width=130)
        self._d_clarity_bar.pack(side="right", padx=10, pady=10)
        self._d_clarity_bar.set(1.0)

        # alert panel
        a_card = ctk.CTkFrame(top, fg_color=CARD, corner_radius=12,
                               border_width=1, border_color=BORDER)
        a_card.pack(side="left", fill="both", expand=True, pady=4)

        ctk.CTkLabel(a_card, text="Live Feedback",
                     text_color=SUBTEXT, font=ctk.CTkFont(size=11)).pack(
                     anchor="w", padx=14, pady=(12, 4))

        self._alert_box = ctk.CTkTextbox(
            a_card, fg_color=CARD2, text_color=TEXT,
            font=ctk.CTkFont(size=12), state="disabled",
            corner_radius=8, border_width=0,
        )
        self._alert_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # ── row 1: chart ───────────────────────────────────────────────
        c_card = ctk.CTkFrame(tab, fg_color=CARD, corner_radius=12,
                               border_width=1, border_color=BORDER)
        c_card.pack(fill="both", expand=True, padx=4, pady=(6, 4))

        ctk.CTkLabel(c_card, text="WPM over time  (last 60 s)",
                     text_color=SUBTEXT, font=ctk.CTkFont(size=11)).pack(
                     anchor="w", padx=14, pady=(8, 0))

        self._chart = ChartWidget(c_card, height=140)
        self._chart.configure_config(self.cfg)
        self._chart.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        # ── row 2: transcript ──────────────────────────────────────────
        t_card = ctk.CTkFrame(tab, fg_color=CARD, corner_radius=12,
                               border_width=1, border_color=BORDER)
        t_card.pack(fill="x", padx=4, pady=(0, 4))

        ctk.CTkLabel(t_card, text="Live Transcript",
                     text_color=SUBTEXT, font=ctk.CTkFont(size=11)).pack(
                     anchor="w", padx=14, pady=(8, 0))

        self._transcript_box = ctk.CTkTextbox(
            t_card, fg_color=CARD2, text_color=TEXT,
            font=ctk.CTkFont(size=12), height=70,
            state="disabled", corner_radius=8, border_width=0,
            wrap="word",
        )
        self._transcript_box.pack(fill="x", padx=10, pady=(4, 10))

    # ── statistics tab ─────────────────────────────────────────────────

    def _build_stats_tab(self):
        tab = self._tabs.tab("Statistics")
        tab.configure(fg_color=BG)

        def big_stat(parent, label, row, col):
            card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=12,
                                border_width=1, border_color=BORDER)
            card.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")
            ctk.CTkLabel(card, text=label, text_color=SUBTEXT,
                         font=ctk.CTkFont(size=11)).pack(pady=(14, 2))
            val = ctk.CTkLabel(card, text="—", text_color=TEXT,
                               font=ctk.CTkFont(size=28, weight="bold"))
            val.pack(pady=(0, 14))
            return val

        grid = ctk.CTkFrame(tab, fg_color=BG)
        grid.pack(fill="both", expand=True, padx=4, pady=4)
        for c in range(3):
            grid.columnconfigure(c, weight=1)
        for r in range(2):
            grid.rowconfigure(r, weight=1)

        self._s_avg_wpm   = big_stat(grid, "Avg WPM",         0, 0)
        self._s_peak_wpm  = big_stat(grid, "Peak WPM",        0, 1)
        self._s_words     = big_stat(grid, "Total Words",      0, 2)
        self._s_duration  = big_stat(grid, "Speaking Time",   1, 0)
        self._s_fast_ev   = big_stat(grid, "Fast Events",     1, 1)
        self._s_clarity   = big_stat(grid, "Avg Clarity",     1, 2)

        ctk.CTkButton(tab, text="Reset Session", height=32, corner_radius=8,
                      fg_color=BORDER, hover_color=RED_C,
                      command=self._reset_session).pack(
                      pady=(0, 8))

    # ── settings tab ───────────────────────────────────────────────────

    def _build_settings_tab(self):
        tab = self._tabs.tab("Settings")
        tab.configure(fg_color=BG)

        scroll = ctk.CTkScrollableFrame(tab, fg_color=BG, corner_radius=0)
        scroll.pack(fill="both", expand=True)

        def section(title):
            ctk.CTkLabel(scroll, text=title, text_color=ACCENT,
                         font=ctk.CTkFont(size=13, weight="bold")).pack(
                         anchor="w", padx=16, pady=(14, 4))

        def slider_row(label, key, lo, hi, step=1):
            f = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=10,
                             height=52)
            f.pack(fill="x", padx=12, pady=3)
            f.pack_propagate(False)
            ctk.CTkLabel(f, text=label, text_color=TEXT,
                         font=ctk.CTkFont(size=12)).pack(side="left", padx=12)
            val_lbl = ctk.CTkLabel(f, text=str(self.cfg.get(key)),
                                   text_color=ACCENT, width=40,
                                   font=ctk.CTkFont(size=12, weight="bold"))
            val_lbl.pack(side="right", padx=12)
            sl = ctk.CTkSlider(f, from_=lo, to=hi, number_of_steps=int((hi-lo)/step),
                               progress_color=ACCENT, button_color=ACCENT)
            sl.set(self.cfg.get(key))
            sl.pack(side="right", padx=8)

            def _cb(v, k=key, lbl=val_lbl):
                iv = int(round(v))
                self.cfg.set(k, iv)
                lbl.configure(text=str(iv))

            sl.configure(command=_cb)

        section("Speed Thresholds (WPM)")
        slider_row("Good pace max",  "wpm_good_max",  80, 200)
        slider_row("Fast warning",   "wpm_fast",     120, 220)
        slider_row("Very fast alert","wpm_very_fast",150, 250)

        section("Clarity")
        slider_row("Low clarity warning (0–100)", "clarity_warn", 20, 80)

        section("Notifications")
        slider_row("Alert after (seconds of fast speech)", "alert_duration",  3, 30)
        slider_row("Notification cooldown (seconds)",      "notification_cooldown", 10, 120)

        section("Speech Recognition")

        # Vosk model picker
        mf = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=10, height=52)
        mf.pack(fill="x", padx=12, pady=3)
        mf.pack_propagate(False)
        ctk.CTkLabel(mf, text="Model (restart to apply)", text_color=TEXT,
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=12)
        model_options = {
            "small-en  (~50 MB, English US)":    "small-en",
            "large-en  (~1.8 GB, English US)":   "large-en",
            "en-india  (~1 GB, English India)":  "en-india",
            "small-de  (~50 MB, German)":        "small-de",
            "small-es  (~50 MB, Spanish)":       "small-es",
            "small-fr  (~50 MB, French)":        "small-fr",
        }
        cur_model_key = self.cfg.get("vosk_model") or "small-en"
        cur_model_label = next(
            (lbl for lbl, key in model_options.items() if key == cur_model_key),
            list(model_options.keys())[0]
        )
        mvar = tk.StringVar(value=cur_model_label)

        def _set_model(label):
            self.cfg.set("vosk_model", model_options.get(label, "small-en"))

        ctk.CTkOptionMenu(mf, variable=mvar,
                          values=list(model_options.keys()),
                          fg_color=CARD2, button_color=ACCENT,
                          command=_set_model, width=260).pack(
                          side="right", padx=12)

        section("Audio Input")
        af = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=10, height=52)
        af.pack(fill="x", padx=12, pady=3)
        af.pack_propagate(False)
        ctk.CTkLabel(af, text="Microphone", text_color=TEXT,
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=12)
        devices   = AudioCapture.list_input_devices()
        dev_names = ["Default"] + [d["name"][:40] for d in devices]
        dev_ids   = [None]     + [d["id"]         for d in devices]
        cur_id    = self.cfg.get("audio_device")
        cur_name  = "Default"
        if cur_id is not None:
            for d in devices:
                if d["id"] == cur_id:
                    cur_name = d["name"][:40]; break
        dvar = tk.StringVar(value=cur_name)

        def _set_device(name):
            idx = dev_names.index(name) if name in dev_names else 0
            self.cfg.set("audio_device", dev_ids[idx])

        dev_opt = ctk.CTkOptionMenu(af, variable=dvar, values=dev_names,
                                    fg_color=CARD2, button_color=ACCENT,
                                    command=_set_device, width=220)
        dev_opt.pack(side="right", padx=12)

        section("Display")
        tf = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=10, height=52)
        tf.pack(fill="x", padx=12, pady=3)
        tf.pack_propagate(False)
        ctk.CTkLabel(tf, text="Always on top (compact)", text_color=TEXT,
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=12)
        sw = ctk.CTkSwitch(tf, text="", onvalue=True, offvalue=False,
                           progress_color=ACCENT,
                           command=lambda: self.cfg.set("always_on_top",
                                                        sw.get()))
        if self.cfg.get("always_on_top"):
            sw.select()
        sw.pack(side="right", padx=12)

    # ────────────────────────────────────────────────────────────────── #
    #  Compact ↔ Dashboard toggle                                        #
    # ────────────────────────────────────────────────────────────────── #

    def _show_compact(self):
        self._df.pack_forget()
        self._cf.pack(fill="both", expand=True, padx=0, pady=0)
        self._compact_mode = True
        self._set_compact_geometry()
        self.root.attributes("-topmost", self.cfg.get("always_on_top"))
        self.root.resizable(False, False)

    def _show_dashboard(self):
        self._cf.pack_forget()
        self._df.pack(fill="both", expand=True)
        self._compact_mode = False
        self._set_dashboard_geometry()
        self.root.attributes("-topmost", False)
        self.root.resizable(True, True)

    def _switch_to_compact(self):
        self._show_compact()

    def _switch_to_dashboard(self):
        self._show_dashboard()

    def _set_compact_geometry(self):
        self.root.geometry(f"{COMPACT_W}x{COMPACT_H}")

    def _set_dashboard_geometry(self):
        self.root.geometry(f"{DASH_W}x{DASH_H}")

    # ── draggable compact window ───────────────────────────────────────

    def _drag_start(self, event):
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _drag_motion(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    # ────────────────────────────────────────────────────────────────── #
    #  Model loading                                                     #
    # ────────────────────────────────────────────────────────────────── #

    def _load_model_async(self):
        self._processor = SpeechProcessor(
            chunk_queue=self._seg_q,
            on_result=self._on_result,
            model_key=self.cfg.get("vosk_model") or "small-en",
        )
        t = threading.Thread(target=self._load_model_thread, daemon=True)
        t.start()

    def _load_model_thread(self):
        def cb(msg):
            self.root.after(0, self._set_status, msg)
        try:
            self._processor.load_model(progress_cb=cb)
            self._model_ready = True
            self.root.after(0, self._set_status, "Ready – click Start")
        except Exception as e:
            self.root.after(0, self._set_status, f"Model error: {e}")

    # ────────────────────────────────────────────────────────────────── #
    #  Monitoring start / stop                                           #
    # ────────────────────────────────────────────────────────────────── #

    def _toggle_monitoring(self):
        if self._monitoring:
            self._stop_monitoring()
        else:
            self._start_monitoring()

    def _start_monitoring(self):
        if not self._model_ready:
            self._set_status("Model not ready yet…")
            return
        device = self.cfg.get("audio_device")
        self._capture = AudioCapture(self._seg_q, device=device)
        try:
            self._capture.start()
        except Exception as e:
            self._set_status(f"Mic error: {e}")
            return
        self._processor.start()
        self._monitoring = True
        self._fast_since = None
        self._set_btn_state(True)
        self._set_status("Monitoring…")
        self._append_alert("▶ Session started.")

    def _stop_monitoring(self):
        self._monitoring = False
        if self._capture:
            self._capture.stop()
            self._capture = None
        if self._processor:
            self._processor.stop()
            self._processor.start()  # restart worker thread for next session
        self._set_btn_state(False)
        self._set_status("Stopped")
        self._append_alert("⏹ Session stopped.")
        self._update_stats_tab()

    # ────────────────────────────────────────────────────────────────── #
    #  Result callback  (runs on processor thread → dispatch to UI)     #
    # ────────────────────────────────────────────────────────────────── #

    def _on_result(self, result: SpeechResult):
        self.root.after(0, self._apply_result, result)

    def _apply_result(self, result: SpeechResult):
        if not self._monitoring:
            return

        wpm     = result.wpm
        clarity = result.clarity
        color   = wpm_color(wpm, self.cfg)
        label   = wpm_label(wpm, self.cfg)

        # ── compact view ───────────────────────────────────────────────
        self._c_wpm_label.configure(text=str(wpm), text_color=color)
        self._c_status_label.configure(text=label, text_color=color)
        self._c_clarity_bar.configure(progress_color=clarity_color(clarity))
        self._c_clarity_bar.set(clarity / 100)

        # ── dashboard: monitor tab ─────────────────────────────────────
        self._gauge.set_wpm(wpm)
        self._d_status.configure(text=label, text_color=color)
        self._d_wpm_val.configure(text=str(wpm), text_color=color)
        self._d_clarity_val.configure(
            text=f"{clarity:.0f}%",
            text_color=clarity_color(clarity))
        self._d_clarity_bar.configure(progress_color=clarity_color(clarity))
        self._d_clarity_bar.set(clarity / 100)

        stats = self._processor.stats
        self._d_words_val.configure(text=str(stats.total_words))
        self._d_avg_val.configure(text=str(stats.avg_wpm))
        self._d_peak_val.configure(text=str(stats.peak_wpm))

        # chart
        self._chart.push(wpm)

        # transcript
        if result.text.strip():
            self._append_transcript(result.text.strip(), wpm, clarity)

        # ── alert logic ────────────────────────────────────────────────
        now = time.time()
        fast_thresh = self.cfg.get("wpm_fast")
        alert_dur   = self.cfg.get("alert_duration")
        clarity_th  = self.cfg.get("clarity_warn")

        if wpm >= fast_thresh:
            if self._fast_since is None:
                self._fast_since = now
            elif now - self._fast_since >= alert_dur:
                msg = f"⚡ Speaking at {wpm} WPM – slow down!"
                self._append_alert(msg)
                self.notifier.notify("Speech Monitor", msg, tag="fast")
                stats.fast_speech_events += 1
                self._fast_since = now  # reset so it can re-alert
        else:
            self._fast_since = None

        if clarity < clarity_th:
            if self._low_clarity_since is None:
                self._low_clarity_since = now
            elif now - self._low_clarity_since >= alert_dur:
                msg = f"⚠ Unclear speech ({clarity:.0f}%) – speak more clearly."
                self._append_alert(msg)
                self.notifier.notify("Speech Monitor", msg, tag="clarity")
                stats.low_clarity_events += 1
                self._low_clarity_since = now
        else:
            self._low_clarity_since = None

        self._update_stats_tab()

    # ────────────────────────────────────────────────────────────────── #
    #  Helper UI updates                                                 #
    # ────────────────────────────────────────────────────────────────── #

    def _set_status(self, msg: str):
        self._c_status_label.configure(text=msg, text_color=SUBTEXT)
        self._d_status.configure(text=msg, text_color=SUBTEXT)

    def _set_btn_state(self, running: bool):
        lbl = "⏹  Stop" if running else "▶  Start"
        col = RED_C if running else ACCENT
        hov = "#c62828" if running else "#9c6dff"
        self._c_toggle_btn.configure(text=lbl, fg_color=col, hover_color=hov)
        self._d_toggle_btn.configure(text=lbl, fg_color=col, hover_color=hov)

    def _append_alert(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self._alert_box.configure(state="normal")
        self._alert_box.insert("end", f"[{ts}] {msg}\n")
        self._alert_box.see("end")
        self._alert_box.configure(state="disabled")

    def _append_transcript(self, text: str, wpm: int, clarity: float):
        self._transcript_box.configure(state="normal")
        self._transcript_box.insert("end", text + " ")
        self._transcript_box.see("end")
        self._transcript_box.configure(state="disabled")
        self._transcript_log.append((text, wpm, clarity))
        # keep last 200 entries
        if len(self._transcript_log) > 200:
            self._transcript_log = self._transcript_log[-200:]

    def _update_stats_tab(self):
        if self._processor is None:
            return
        s = self._processor.stats
        self._s_avg_wpm.configure(text=str(s.avg_wpm))
        self._s_peak_wpm.configure(text=str(s.peak_wpm))
        self._s_words.configure(text=str(s.total_words))
        mins = int(s.total_speech_sec // 60)
        secs = int(s.total_speech_sec % 60)
        self._s_duration.configure(text=f"{mins}m {secs:02d}s")
        self._s_fast_ev.configure(text=str(s.fast_speech_events))
        # average clarity from transcript log
        if self._transcript_log:
            avg_c = sum(c for _, _, c in self._transcript_log) / len(self._transcript_log)
            self._s_clarity.configure(
                text=f"{avg_c:.0f}%",
                text_color=clarity_color(avg_c))

    def _reset_session(self):
        if self._processor:
            self._processor.reset_session()
        self._transcript_log.clear()
        self._transcript_box.configure(state="normal")
        self._transcript_box.delete("1.0", "end")
        self._transcript_box.configure(state="disabled")
        self._alert_box.configure(state="normal")
        self._alert_box.delete("1.0", "end")
        self._alert_box.configure(state="disabled")
        self._chart._history.clear()
        self._chart._draw()
        self._gauge.set_wpm(0)
        self._c_wpm_label.configure(text="0", text_color=SUBTEXT)
        self._append_alert("↺ Session reset.")

    # ────────────────────────────────────────────────────────────────── #
    #  Lifecycle                                                         #
    # ────────────────────────────────────────────────────────────────── #

    def _on_close(self):
        if self._monitoring:
            self._stop_monitoring()
        if self._processor:
            self._processor.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
