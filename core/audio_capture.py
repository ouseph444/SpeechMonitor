import numpy as np
import sounddevice as sd
import queue

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK = 1024     # ~64 ms per chunk — fed directly to Vosk's streaming API


class AudioCapture:
    def __init__(self, chunk_queue: queue.Queue, device=None):
        self.chunk_queue = chunk_queue
        self.device = device
        self._stream = None
        self._running = False

    def start(self):
        self._running = True
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            blocksize=CHUNK,
            dtype=np.float32,
            device=self.device,
            callback=self._cb,
        )
        self._stream.start()

    def stop(self):
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _cb(self, indata, frames, time_info, status):
        # Send every chunk to processor — Vosk handles its own VAD internally
        self.chunk_queue.put(indata[:, 0].copy())

    @staticmethod
    def list_input_devices():
        devices = sd.query_devices()
        return [
            {"id": i, "name": d["name"]}
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ]
