"""
Speech Monitor
──────────────
Real-time WPM + clarity monitor for online presentations and interviews.
Run:  python main.py
"""

import sys
import os

# Ensure project root is on the path when run from another directory
sys.path.insert(0, os.path.dirname(__file__))

import customtkinter as ctk

from core.config import Config
from ui.app import SpeechMonitorApp


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    config = Config()
    app = SpeechMonitorApp(config)
    app.run()


if __name__ == "__main__":
    main()
