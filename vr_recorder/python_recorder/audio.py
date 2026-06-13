"""Audio playback abstraction for the recorder.

Song playback must share a clock with motion capture (see doc §5.3/§6). No audio
backend is available in the development environment, so the default
:class:`NullAudioPlayer` is a no-op that logs a warning -- the recorder still
produces a valid (silent) capture. A real backend (e.g. ``ffplay`` or
``sounddevice``) plugs in behind the same interface.

This is intentionally minimal: audio-latency calibration (aligning ``song t0``
with the first audible sample) is a Phase-2 task and is NOT handled here.
"""

from __future__ import annotations

import shutil
import subprocess
import warnings
from typing import Optional


class AudioPlayer:
    """Interface: ``play(path)`` starts playback, ``stop()`` halts it."""

    name = "abstract"
    available = False

    def play(self, song_path: str) -> None:
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - trivial
        pass


class NullAudioPlayer(AudioPlayer):
    """No-op player. Records proceed in silence; song timing is logical only."""

    name = "null"
    available = True

    def play(self, song_path: str) -> None:
        warnings.warn(
            f"No audio backend available; not playing {song_path!r}. The capture "
            "will be silent and song time is taken from the logical sample clock.",
            stacklevel=2,
        )

    def stop(self) -> None:
        pass


class FfplayAudioPlayer(AudioPlayer):
    """Plays audio via the ``ffplay`` CLI if present. Best-effort, no latency cal."""

    name = "ffplay"

    def __init__(self) -> None:
        self._exe = shutil.which("ffplay")
        self.available = self._exe is not None
        self._proc: Optional[subprocess.Popen] = None

    def play(self, song_path: str) -> None:  # pragma: no cover - needs ffplay
        if not self._exe:
            raise RuntimeError("ffplay not found on PATH")
        self._proc = subprocess.Popen(
            [self._exe, "-nodisp", "-autoexit", "-loglevel", "quiet", song_path]
        )

    def stop(self) -> None:  # pragma: no cover - needs ffplay
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


def best_available_player() -> AudioPlayer:
    """Return ffplay if installed, else a warning-emitting no-op player."""
    ff = FfplayAudioPlayer()
    return ff if ff.available else NullAudioPlayer()
