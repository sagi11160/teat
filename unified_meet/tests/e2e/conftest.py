from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
import pytest

PROJECT_DIR = Path(__file__).resolve().parents[2]
CSRF = {"X-MeetBridge": "1"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class LiveServer:
    base_url: str
    db_path: Path
    log_path: Path
    env: dict
    proc: subprocess.Popen | None = None

    def start(self) -> None:
        log = open(self.log_path, "ab")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "meetbridge.main"], cwd=PROJECT_DIR, env=self.env, stdout=log, stderr=subprocess.STDOUT
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited early, see {self.log_path}")
            try:
                if httpx.get(f"{self.base_url}/readyz", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError("server did not become ready")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, headers=CSRF, timeout=10)


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("live")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "MB_ENV": "test",
        "MB_PORT": str(port),
        "MB_HOST": "127.0.0.1",
        "MB_PUBLIC_BASE_URL": base,
        "MB_DATABASE_URL": f"sqlite:///{tmp / 'live.db'}",
        "MB_SECRET_KEY": "e2e-secret-key-0123456789-abcdefghijklmnop",
        "MB_ENABLE_MOCK_PROVIDER": "true",
        "MB_ICE_SERVERS": "[]",
        "MB_SUPERVISOR_INTERVAL_SECONDS": "0.5",
        "MB_BOT_JOIN_TIMEOUT_SECONDS": "30",
        "MB_BOT_ADMISSION_TIMEOUT_SECONDS": "60",
        "MB_LOG_LEVEL": "INFO",
    }
    server = LiveServer(base, tmp / "live.db", tmp / "server.log", env)
    server.start()
    yield server
    server.stop()


def write_tone(path: Path, freq: float, seconds: float = 10.0, rate: int = 48000) -> Path:
    t = np.arange(int(seconds * rate)) / rate
    samples = (0.5 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return path


@pytest.fixture(scope="module")
def playwright_instance():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def human(playwright_instance, tmp_path_factory):
    """Factory: a browser whose microphone plays a pure tone at ``freq`` Hz."""
    tmp = tmp_path_factory.mktemp("tones")
    browsers = []

    def make(freq: float):
        wav = write_tone(tmp / f"tone_{int(freq)}.wav", freq)
        browser = playwright_instance.chromium.launch(
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={wav}",
                "--autoplay-policy=no-user-gesture-required",
            ]
        )
        browsers.append(browser)
        context = browser.new_context(permissions=["microphone", "camera"])
        return context.new_page()

    yield make
    for b in browsers:
        b.close()
