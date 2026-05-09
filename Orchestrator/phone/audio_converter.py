#!/usr/bin/env python3
"""
audio_converter.py - Phone Audio Format Conversion

Handles bidirectional conversion between phone audio (8kHz G.711 mu-law)
and AI API audio formats (16-24kHz PCM16).

Phone standard: 8000 Hz, 8-bit mu-law (G.711)
OpenAI/Grok: 24000 Hz, 16-bit PCM (little-endian)
Gemini: 16000 Hz input, 24000 Hz output, 16-bit PCM

Conversion flow:
    Phone → AI: ULAW decode → Upsample (3x for 24kHz, 2x for 16kHz)
    AI → Phone: Low-pass filter → Downsample → ULAW encode

Audio Quality:
    Uses scipy for high-quality resampling with proper anti-aliasing.
    Falls back to simple interpolation/averaging if scipy unavailable.
"""

import struct
import math
from typing import Tuple
import numpy as np

# Try to import scipy for high-quality resampling
try:
    from scipy import signal
    from scipy.signal import resample_poly, decimate
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    print("[AudioConverter] scipy not available - using basic resampling (lower quality)")


class AudioConverter:
    """
    Bidirectional audio format converter for phone ↔ AI bridging.

    Converts between:
    - 8kHz 8-bit mu-law (G.711) for phone
    - 16kHz/24kHz 16-bit PCM for AI APIs
    """

    # Mu-law constants
    MULAW_BIAS = 33
    MULAW_MAX = 32635
    MULAW_CLIP = 32635

    # Mu-law decoding table (8-bit mu-law to 16-bit PCM)
    MULAW_DECODE_TABLE = None

    @classmethod
    def _init_decode_table(cls):
        """Initialize mu-law decode lookup table."""
        if cls.MULAW_DECODE_TABLE is not None:
            return

        cls.MULAW_DECODE_TABLE = []
        for mu in range(256):
            # Invert bits
            mu_inverted = ~mu & 0xFF

            # Extract sign, exponent, mantissa
            sign = (mu_inverted & 0x80) >> 7
            exponent = (mu_inverted & 0x70) >> 4
            mantissa = mu_inverted & 0x0F

            # Compute PCM value
            pcm = ((mantissa << 3) + cls.MULAW_BIAS) << exponent
            pcm -= cls.MULAW_BIAS

            # Apply sign
            if sign:
                pcm = -pcm

            cls.MULAW_DECODE_TABLE.append(pcm)

    @classmethod
    def ulaw_to_pcm16(cls, ulaw_byte: int) -> int:
        """
        Decode a single mu-law byte to 16-bit PCM.

        Args:
            ulaw_byte: 8-bit mu-law encoded value (0-255)

        Returns:
            16-bit signed PCM value (-32768 to 32767)
        """
        cls._init_decode_table()
        return cls.MULAW_DECODE_TABLE[ulaw_byte & 0xFF]

    @classmethod
    def pcm16_to_ulaw(cls, pcm_sample: int) -> int:
        """
        Encode a 16-bit PCM sample to mu-law.

        Args:
            pcm_sample: 16-bit signed PCM value

        Returns:
            8-bit mu-law encoded value
        """
        # Determine sign
        sign = 0
        if pcm_sample < 0:
            sign = 0x80
            pcm_sample = -pcm_sample

        # Clip to valid range
        if pcm_sample > cls.MULAW_CLIP:
            pcm_sample = cls.MULAW_CLIP

        # Add bias
        pcm_sample += cls.MULAW_BIAS

        # Find exponent (highest bit position)
        exponent = 7
        exp_mask = 0x4000
        while exponent > 0:
            if pcm_sample & exp_mask:
                break
            exponent -= 1
            exp_mask >>= 1

        # Extract mantissa
        mantissa = (pcm_sample >> (exponent + 3)) & 0x0F

        # Combine and invert
        mu = ~(sign | (exponent << 4) | mantissa) & 0xFF

        return mu

    @classmethod
    def ulaw_bytes_to_pcm16(cls, ulaw_data: bytes) -> bytes:
        """
        Decode mu-law audio buffer to PCM16.

        Args:
            ulaw_data: Bytes of 8-bit mu-law encoded audio

        Returns:
            Bytes of 16-bit PCM audio (little-endian)
        """
        cls._init_decode_table()

        pcm_samples = []
        for byte in ulaw_data:
            pcm_samples.append(cls.MULAW_DECODE_TABLE[byte])

        # Pack as little-endian 16-bit integers
        return struct.pack(f"<{len(pcm_samples)}h", *pcm_samples)

    @classmethod
    def pcm16_to_ulaw_bytes(cls, pcm_data: bytes) -> bytes:
        """
        Encode PCM16 audio buffer to mu-law.

        Args:
            pcm_data: Bytes of 16-bit PCM audio (little-endian)

        Returns:
            Bytes of 8-bit mu-law encoded audio
        """
        # Unpack PCM samples
        num_samples = len(pcm_data) // 2
        pcm_samples = struct.unpack(f"<{num_samples}h", pcm_data)

        # Encode each sample
        ulaw_bytes = bytearray()
        for sample in pcm_samples:
            ulaw_bytes.append(cls.pcm16_to_ulaw(sample))

        return bytes(ulaw_bytes)

    @staticmethod
    def upsample(pcm_data: bytes, factor: int) -> bytes:
        """
        Upsample PCM16 audio by integer factor with anti-imaging filter.

        Uses scipy.signal.resample_poly when available for high-quality
        upsampling with proper interpolation filtering.

        Args:
            pcm_data: Input PCM16 bytes (little-endian)
            factor: Upsampling factor (2 or 3)

        Returns:
            Upsampled PCM16 bytes
        """
        if factor == 1:
            return pcm_data

        # Unpack input samples
        num_samples = len(pcm_data) // 2
        if num_samples == 0:
            return b""

        samples = struct.unpack(f"<{num_samples}h", pcm_data)

        if SCIPY_AVAILABLE and num_samples > 10:
            # High-quality upsampling with scipy
            # resample_poly uses polyphase filtering for efficient, high-quality resampling
            samples_array = np.array(samples, dtype=np.float64)

            try:
                # Upsample by factor, downsample by 1 = pure upsampling
                upsampled = resample_poly(samples_array, factor, 1)
                # Clip to int16 range and convert
                upsampled = np.clip(upsampled, -32768, 32767).astype(np.int16)
                return upsampled.tobytes()
            except Exception:
                # Fall back to simple method if scipy fails
                pass

        # Fallback: Linear interpolation
        output = []
        for i in range(len(samples) - 1):
            s0 = samples[i]
            s1 = samples[i + 1]

            # Add original sample
            output.append(s0)

            # Add interpolated samples
            for j in range(1, factor):
                interp = s0 + (s1 - s0) * j // factor
                output.append(interp)

        # Add final sample
        output.append(samples[-1])

        # Pack output
        return struct.pack(f"<{len(output)}h", *output)

    @staticmethod
    def downsample(pcm_data: bytes, factor: int) -> bytes:
        """
        Downsample PCM16 audio by integer factor with anti-aliasing.

        Uses scipy.signal.decimate when available for high-quality downsampling
        with proper low-pass filtering to prevent aliasing artifacts.

        Args:
            pcm_data: Input PCM16 bytes (little-endian)
            factor: Downsampling factor (2, 3, or 6)

        Returns:
            Downsampled PCM16 bytes
        """
        if factor == 1:
            return pcm_data

        # Unpack input samples
        num_samples = len(pcm_data) // 2
        if num_samples == 0:
            return b""

        samples = struct.unpack(f"<{num_samples}h", pcm_data)

        if SCIPY_AVAILABLE and num_samples > 100:
            # High-quality downsampling with scipy
            # decimate() applies an anti-aliasing low-pass filter before downsampling
            samples_array = np.array(samples, dtype=np.float64)

            try:
                # Use FIR filter for longer signals (better quality, requires more samples)
                # FIR with n=30 taps provides good anti-aliasing with reasonable latency
                if num_samples > 300:
                    # For longer chunks, use zero-phase FIR (best quality)
                    downsampled = decimate(samples_array, factor, n=30, ftype='fir', zero_phase=True)
                else:
                    # For shorter chunks, use non-zero-phase to avoid padding issues
                    downsampled = decimate(samples_array, factor, n=20, ftype='fir', zero_phase=False)

                # Clip to int16 range and convert
                downsampled = np.clip(downsampled, -32768, 32767).astype(np.int16)
                return downsampled.tobytes()
            except Exception:
                # Fall back to simple method if scipy fails
                pass

        # Fallback: Simple averaging (basic low-pass filter)
        output = []
        for i in range(0, len(samples) - factor + 1, factor):
            total = sum(samples[i:i + factor])
            output.append(total // factor)

        return struct.pack(f"<{len(output)}h", *output)

    @staticmethod
    def stereo_to_mono(pcm_data: bytes) -> bytes:
        """
        Convert stereo PCM16 audio to mono by averaging L+R channels.

        Args:
            pcm_data: Stereo PCM16 bytes (interleaved L,R,L,R,...)

        Returns:
            Mono PCM16 bytes
        """
        # Unpack stereo samples (L, R, L, R, ...)
        num_samples = len(pcm_data) // 2
        if num_samples == 0:
            return b""

        samples = struct.unpack(f"<{num_samples}h", pcm_data)

        # Average pairs of samples (L+R)/2
        mono_samples = []
        for i in range(0, len(samples) - 1, 2):
            left = samples[i]
            right = samples[i + 1]
            mono_samples.append((left + right) // 2)

        return struct.pack(f"<{len(mono_samples)}h", *mono_samples)

    @classmethod
    def phone_to_ai(cls, ulaw_data: bytes, target_rate: int = 24000) -> bytes:
        """
        Convert phone audio to AI format.

        8kHz ULAW → 16/24kHz PCM16

        Args:
            ulaw_data: Phone audio (8kHz mu-law)
            target_rate: Target sample rate (16000 or 24000)

        Returns:
            PCM16 audio at target rate
        """
        # Decode mu-law to PCM16
        pcm_8k = cls.ulaw_bytes_to_pcm16(ulaw_data)

        # Upsample to target rate
        if target_rate == 24000:
            return cls.upsample(pcm_8k, 3)  # 8000 * 3 = 24000
        elif target_rate == 16000:
            return cls.upsample(pcm_8k, 2)  # 8000 * 2 = 16000
        else:
            raise ValueError(f"Unsupported target rate: {target_rate}")

    @classmethod
    def ai_to_phone(cls, pcm_data: bytes, source_rate: int = 24000, stereo: bool = False) -> bytes:
        """
        Convert AI/media audio to phone format.

        Supports: 16kHz, 24kHz, 48kHz (mono or stereo) → 8kHz ULAW

        Args:
            pcm_data: Audio data (PCM16 at source_rate)
            source_rate: Source sample rate (16000, 24000, or 48000)
            stereo: If True, convert stereo to mono first

        Returns:
            Phone audio (8kHz mu-law)
        """
        # Convert stereo to mono if needed
        if stereo:
            pcm_data = cls.stereo_to_mono(pcm_data)

        # Downsample to 8kHz
        if source_rate == 48000:
            # 48kHz → 8kHz: two-stage for better quality (48→24→8)
            pcm_24k = cls.downsample(pcm_data, 2)  # 48000 / 2 = 24000
            pcm_8k = cls.downsample(pcm_24k, 3)    # 24000 / 3 = 8000
        elif source_rate == 24000:
            pcm_8k = cls.downsample(pcm_data, 3)  # 24000 / 3 = 8000
        elif source_rate == 16000:
            pcm_8k = cls.downsample(pcm_data, 2)  # 16000 / 2 = 8000
        elif source_rate == 8000:
            pcm_8k = pcm_data  # Already at target rate
        else:
            raise ValueError(f"Unsupported source rate: {source_rate}. Supported: 8000, 16000, 24000, 48000")

        # Encode to mu-law
        return cls.pcm16_to_ulaw_bytes(pcm_8k)

    @classmethod
    def get_sample_rate_for_backend(cls, backend: str) -> Tuple[int, int]:
        """
        Get input/output sample rates for an AI backend.

        Args:
            backend: AI backend name

        Returns:
            Tuple of (input_rate, output_rate)
        """
        rates = {
            "openai_realtime": (24000, 24000),
            "gemini_live": (16000, 24000),
            "grok_live": (24000, 24000),
            "claude_code": (24000, 24000),  # Uses OpenAI TTS
        }
        return rates.get(backend, (24000, 24000))

    @classmethod
    def generate_tone(cls, frequency: int = 440, duration_ms: int = 200,
                      sample_rate: int = 24000, volume: float = 0.3) -> bytes:
        """
        Generate a simple sine wave tone as ULAW audio for phone playback.

        Generates at 24kHz (same as TTS) and uses ai_to_phone() for consistent
        quality. The downsampling acts as a low-pass filter for smoother audio.

        Args:
            frequency: Tone frequency in Hz (default 440 = A4)
            duration_ms: Duration in milliseconds
            sample_rate: Generation sample rate (24000 for TTS-quality)
            volume: Volume level 0.0-1.0 (default 0.3 for comfortable level)

        Returns:
            ULAW encoded audio bytes (8kHz for phone)
        """
        num_samples = int(sample_rate * duration_ms / 1000)

        # Generate sine wave as PCM16 at high sample rate
        pcm_samples = []
        for i in range(num_samples):
            t = i / sample_rate
            # Sine wave with fade in/out to avoid clicks
            fade_samples = min(num_samples // 10, 300)  # More samples at higher rate
            envelope = 1.0
            if i < fade_samples:
                envelope = i / fade_samples
            elif i > num_samples - fade_samples:
                envelope = (num_samples - i) / fade_samples

            if frequency == 0:
                sample = 0  # Silence
            else:
                sample = int(32767 * volume * envelope * math.sin(2 * math.pi * frequency * t))
            pcm_samples.append(max(-32768, min(32767, sample)))

        # Pack as PCM16
        pcm_data = struct.pack(f"<{len(pcm_samples)}h", *pcm_samples)

        # Use same conversion path as TTS for consistent quality
        return cls.ai_to_phone(pcm_data, sample_rate)

    @classmethod
    def generate_ready_tone(cls) -> bytes:
        """
        Generate a pleasant 'ready' tone indicating the AI is connected.
        Two quick ascending tones.

        Returns:
            ULAW encoded audio bytes for phone playback
        """
        # First tone (lower)
        tone1 = cls.generate_tone(frequency=523, duration_ms=100, volume=0.25)  # C5
        # Short silence
        silence = cls.generate_tone(frequency=0, duration_ms=50, volume=0.0)
        # Second tone (higher)
        tone2 = cls.generate_tone(frequency=659, duration_ms=150, volume=0.25)  # E5

        return tone1 + silence + tone2

    @classmethod
    def generate_silence(cls, duration_ms: int = 100, sample_rate: int = 8000) -> bytes:
        """
        Generate silence as ULAW audio.

        Args:
            duration_ms: Duration in milliseconds
            sample_rate: Sample rate in Hz (default 8000 for phone)

        Returns:
            ULAW encoded silence bytes
        """
        num_samples = int(sample_rate * duration_ms / 1000)
        # ULAW silence is 0xFF (mu-law encoding of 0)
        return bytes([0xFF] * num_samples)
