"""TokenBudget — approximate context-window tracker for proactive rotation.

The Gemini Live API doesn't expose a "tokens used" counter at any moment,
but we control the things going IN: audio seconds (both directions, since
the model both hears and speaks) and ambient JPEG frames. Using documented
per-token rates, we estimate usage and signal `should_rotate` at threshold%
of the configured window.

Conservative by design — over-rotating is fine (operator hears a brief
audio gap covered by the resumption-handle reconnect), under-rotating
risks a hard GoAway mid-sentence or context starvation that makes the
model lose track of the operator's request.

Initial constants are documented estimates. Task 8's bench session
records the actual rates per minute on this hardware; refine as needed.
"""
from __future__ import annotations


class TokenBudget:
    def __init__(
        self,
        *,
        window_tokens: int = 32000,
        threshold: float = 0.8,
        audio_tokens_per_s: float = 25.0,    # input + output combined
        # Empirically ~100 tok/frame for low-res pantilt JPEGs on the
        # native-audio model. Original 250.0 was conservative ("bench
        # will calibrate") and caused proactive rotation to fire ~80%
        # within 2 minutes once watch mode was active. 100.0 tracks
        # closer to actual cost and lets sessions run longer.
        jpeg_tokens_per_frame: float = 100.0,
    ) -> None:
        self._window = float(window_tokens)
        self._threshold = float(threshold)
        self._a_tps = audio_tokens_per_s
        self._j_tpf = jpeg_tokens_per_frame
        self._used = 0.0

    @property
    def used_tokens(self) -> float:
        return self._used

    @property
    def usage_pct(self) -> float:
        return self._used / self._window if self._window > 0 else 0.0

    @property
    def should_rotate(self) -> bool:
        return self._window > 0 and self.usage_pct >= self._threshold

    def audio_seconds(self, s: float) -> None:
        self._used += s * self._a_tps

    def jpeg_frames(self, n: int) -> None:
        self._used += n * self._j_tpf

    def reset(self) -> None:
        self._used = 0.0
