import json
import os

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".speechmonitor_config.json")

DEFAULTS = {
    "wpm_slow": 100,
    "wpm_good_max": 150,
    "wpm_fast": 170,
    "wpm_very_fast": 190,
    "clarity_warn": 60,
    "alert_duration": 8,        # seconds of sustained fast speech before alert
    "notification_cooldown": 30,
    "vosk_model": "small-en",
    "audio_device": None,
    "always_on_top": True,
    "start_minimized": False,
}


class Config:
    def __init__(self):
        self._data = dict(DEFAULTS)
        self.load()

    def load(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r") as f:
                    saved = json.load(f)
                self._data.update(saved)
            except Exception:
                pass

    def save(self):
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass

    def get(self, key):
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key, value):
        self._data[key] = value
        self.save()
