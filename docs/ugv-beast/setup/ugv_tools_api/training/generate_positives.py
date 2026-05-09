#!/usr/bin/env python3
"""Generate positive training samples for 'black box flight recorder' via Piper TTS.

Produces ~N_PER_VOICE × N_VOICES × N_VARIATIONS clips at 16kHz, 2-3s each,
with the phrase positioned at the end (typical wake-word placement).
"""
import argparse
import os
import random
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

# Multiple ways to say the phrase (adds diversity to positive set)
POSITIVE_PHRASES = [
    "black box flight recorder",
    "Black Box Flight Recorder",
    "black box, flight recorder",
    "hey, black box flight recorder",
    "okay, black box flight recorder",
    "the black box flight recorder",
]

# Voice configs: (path, length_scales, noise_scales, noise_w_scales)
# Piper knobs: length-scale slower>faster, noise adds variation, noise-w adds prosody variance.
VOICES = [
    "en_US-amy-medium",
    "en_US-lessac-medium",
    "en_US-ryan-high",
    "en_US-kathleen-low",
    "en_US-joe-medium",
    "en_GB-alan-medium",
    "en_GB-jenny_dioco-medium",
]

LENGTH_SCALES = [0.85, 1.0, 1.15, 1.3]   # faster → slower
NOISE_SCALES = [0.5, 0.667, 0.8]
NOISE_W_SCALES = [0.6, 0.8, 1.0]


def synth_one(piper_bin: str, voice_model: str, phrase: str, length_scale: float,
              noise_scale: float, noise_w: float, out_wav: Path) -> bool:
    """Synthesize a single utterance with Piper."""
    cmd = [
        piper_bin,
        "-m", voice_model,
        "--length-scale", str(length_scale),
        "--noise-scale", str(noise_scale),
        "--noise-w-scale", str(noise_w),
        "-f", str(out_wav),
    ]
    try:
        proc = subprocess.run(cmd, input=phrase, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            print(f"Piper fail: {proc.stderr[:200]}", file=sys.stderr)
            return False
        return out_wav.exists() and out_wav.stat().st_size > 1000
    except Exception as e:
        print(f"synth err: {e}", file=sys.stderr)
        return False


def post_process(wav_path: Path, target_sr: int = 16000,
                 min_dur: float = 3.0, max_dur: float = 3.5,
                 random_pad: bool = True, rng: random.Random = None) -> np.ndarray:
    """Load wav, resample to 16kHz, pad/trim, add silence before phrase for realism."""
    audio, sr = sf.read(wav_path)
    if audio.ndim > 1:
        audio = audio[:, 0]
    audio = audio.astype(np.float32)

    # Normalize peak
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak * 0.9

    # Resample to 16kHz
    if sr != target_sr:
        audio = resample_poly(audio, target_sr, sr).astype(np.float32)
        sr = target_sr

    # Trim leading/trailing silence
    thresh = 0.005
    nonzero = np.where(np.abs(audio) > thresh)[0]
    if len(nonzero):
        audio = audio[nonzero[0]:nonzero[-1] + 1]

    # Total target length: random between min_dur and max_dur
    total_samples = int(rng.uniform(min_dur, max_dur) * sr) if rng else int(max_dur * sr)
    if len(audio) >= total_samples:
        audio = audio[:total_samples]
    else:
        pad_total = total_samples - len(audio)
        # Place phrase at END (typical wake-word input pattern): pad is prepended
        pad_before = pad_total if not random_pad else int(rng.uniform(0.4, 0.95) * pad_total)
        pad_after = pad_total - pad_before
        audio = np.concatenate([
            np.zeros(pad_before, dtype=np.float32),
            audio,
            np.zeros(pad_after, dtype=np.float32),
        ])

    return audio


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voices-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--piper-bin", default="piper")
    ap.add_argument("--n-per-voice", type=int, default=80,
                    help="Number of clips per voice (phrase x length x noise variations)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path("/tmp/oww_train_pos_raw")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for voice in VOICES:
        voice_model = Path(args.voices_dir) / f"{voice}.onnx"
        if not voice_model.exists():
            print(f"[skip] missing voice: {voice_model}")
            continue
        print(f"[voice] {voice}")
        for i in range(args.n_per_voice):
            phrase = rng.choice(POSITIVE_PHRASES)
            length_scale = rng.choice(LENGTH_SCALES)
            noise = rng.choice(NOISE_SCALES)
            noise_w = rng.choice(NOISE_W_SCALES)
            raw_wav = tmp_dir / f"{voice}_{i:04d}.wav"
            if not synth_one(args.piper_bin, str(voice_model), phrase,
                             length_scale, noise, noise_w, raw_wav):
                continue
            try:
                audio = post_process(raw_wav, rng=rng)
                out_wav = out_dir / f"pos_{voice}_{i:04d}.wav"
                sf.write(out_wav, audio, 16000, subtype="PCM_16")
                count += 1
            except Exception as e:
                print(f"post err: {e}")
            finally:
                raw_wav.unlink(missing_ok=True)
        print(f"  generated {count} total so far")
    print(f"[done] total positive clips: {count}")


if __name__ == "__main__":
    main()
