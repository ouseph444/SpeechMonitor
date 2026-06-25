"""
Real-time speech processing via Vosk (offline, streaming).

Vosk streams audio chunk-by-chunk and fires a callback each time it
detects the end of an utterance. This gives 0.5–2 s latency, which is
ideal for a WPM monitor.
"""

import json
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

SAMPLE_RATE = 16000
WPM_WINDOW_SEC = 60    # rolling window for avg WPM
MIN_WORDS = 2          # ignore single-word fragments

# Vosk model names (auto-downloaded to ~/.cache/vosk on first use)
VOSK_MODELS = {
    "small-en":   "vosk-model-small-en-us-0.15",   # ~50 MB, fast
    "large-en":   "vosk-model-en-us-0.22",          # ~1.8 GB, more accurate
    "en-india":   "vosk-model-en-in-0.5",           # ~1 GB
    "small-de":   "vosk-model-small-de-0.15",
    "small-es":   "vosk-model-small-es-0.42",
    "small-fr":   "vosk-model-small-fr-0.22",
}
DEFAULT_MODEL_KEY = "small-en"


@dataclass
class SpeechResult:
    text: str
    wpm: int
    clarity: float       # 0–100
    speech_sec: float
    timestamp: float


@dataclass
class SessionStats:
    total_words: int = 0
    total_speech_sec: float = 0.0
    peak_wpm: int = 0
    fast_speech_events: int = 0
    low_clarity_events: int = 0
    start_time: float = field(default_factory=time.time)

    @property
    def avg_wpm(self) -> int:
        if self.total_speech_sec < 2:
            return 0
        return int(self.total_words / self.total_speech_sec * 60)

    @property
    def elapsed_sec(self) -> float:
        return time.time() - self.start_time


class SpeechProcessor:
    def __init__(
        self,
        chunk_queue: queue.Queue,
        on_result: Callable[[SpeechResult], None],
        model_key: str = DEFAULT_MODEL_KEY,
    ):
        self.chunk_queue  = chunk_queue
        self.on_result    = on_result
        self.model_key    = model_key

        self._rec      = None   # vosk.KaldiRecognizer
        self._running  = False
        self._thread: Optional[threading.Thread] = None

        self._window: deque = deque()      # (timestamp, words, secs)
        self.current_wpm     = 0
        self.current_clarity = 100.0
        self.stats           = SessionStats()

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def load_model(self, progress_cb: Optional[Callable[[str], None]] = None):
        from vosk import Model, KaldiRecognizer, SetLogLevel   # type: ignore
        SetLogLevel(-1)  # suppress vosk console output

        model_name = VOSK_MODELS.get(self.model_key, VOSK_MODELS[DEFAULT_MODEL_KEY])
        if progress_cb:
            progress_cb(f"Loading model '{model_name}'\n(first run downloads ~50 MB)…")

        model = Model(model_name=model_name)
        self._rec = KaldiRecognizer(model, SAMPLE_RATE)
        self._rec.SetWords(True)   # enables per-word confidence scores

        if progress_cb:
            progress_cb("Model ready – click Start.")

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def reset_session(self):
        """Re-create the recognizer so timestamps reset, clear all session data."""
        self._window.clear()
        self.current_wpm     = 0
        self.current_clarity = 100.0
        self.stats           = SessionStats()
        if self._rec is not None:
            # Flush any pending partial result
            self._rec.FinalResult()

    # ------------------------------------------------------------------ #
    #  Worker                                                              #
    # ------------------------------------------------------------------ #

    def _worker(self):
        while self._running:
            try:
                chunk = self.chunk_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if self._rec is not None:
                self._feed(chunk)

    def _feed(self, audio: np.ndarray):
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        if self._rec.AcceptWaveform(pcm):
            # End of utterance — process final result
            raw = json.loads(self._rec.Result())
            self._handle(raw)
        # Partial results are ignored (we only act on completed utterances)

    def _handle(self, raw: dict):
        text  = raw.get("text", "").strip()
        words = len(text.split()) if text else 0
        if words < MIN_WORDS:
            return

        word_data = raw.get("result", [])

        if word_data:
            t0     = word_data[0].get("start", 0.0)
            t1     = word_data[-1].get("end", t0)
            secs   = max(0.1, t1 - t0)
            conf   = sum(w.get("conf", 1.0) for w in word_data) / len(word_data)
        else:
            secs   = words / 2.5   # rough estimate (150 WPM default)
            conf   = 0.8

        clarity = round(min(100.0, max(0.0, conf * 100)), 1)

        self._window.append((time.time(), words, secs))
        self.stats.total_words     += words
        self.stats.total_speech_sec += secs

        wpm = self._rolling_wpm()
        self.current_wpm     = wpm
        self.current_clarity = clarity
        if wpm > self.stats.peak_wpm:
            self.stats.peak_wpm = wpm

        self.on_result(SpeechResult(
            text=text, wpm=wpm, clarity=clarity,
            speech_sec=secs, timestamp=time.time(),
        ))

    def _rolling_wpm(self) -> int:
        now    = time.time()
        cutoff = now - WPM_WINDOW_SEC
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()
        if not self._window:
            return 0
        total_w = sum(e[1] for e in self._window)
        total_s = sum(e[2] for e in self._window)
        return int(total_w / total_s * 60) if total_s >= 1 else 0
