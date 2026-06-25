import platform
import subprocess
import threading
import time


class Notifier:
    def __init__(self, cooldown_sec: int = 30):
        self.cooldown_sec = cooldown_sec
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def notify(self, title: str, message: str, tag: str = "default"):
        now = time.time()
        with self._lock:
            if now - self._last.get(tag, 0) < self.cooldown_sec:
                return
            self._last[tag] = now
        threading.Thread(target=self._send, args=(title, message), daemon=True).start()

    @staticmethod
    def _send(title: str, message: str):
        system = platform.system()
        try:
            if system == "Darwin":
                # macOS — use osascript; no extra dependencies needed
                script = f'display notification "{message}" with title "{title}"'
                subprocess.run(["osascript", "-e", script],
                               capture_output=True, timeout=5)
            elif system == "Windows":
                from plyer import notification
                notification.notify(title=title, message=message,
                                    app_name="Speech Monitor", timeout=5)
            else:
                # Linux — try notify-send
                subprocess.run(["notify-send", title, message],
                               capture_output=True, timeout=5)
        except Exception:
            pass
