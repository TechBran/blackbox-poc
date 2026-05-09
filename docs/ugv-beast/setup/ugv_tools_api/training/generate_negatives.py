#!/usr/bin/env python3
"""Generate negative training samples — arbitrary phrases that do NOT contain the wake word.

Mix of:
 - Piper TTS with common English phrases
 - Adversarial phrases (contain 'black' or 'box' or 'recorder' alone, or similar-sounding)
 - Silence + low-level noise
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

# Generic English phrases (~100) — model needs to reject these
GENERIC_PHRASES = [
    "hello how are you today",
    "what time is it",
    "turn on the lights",
    "play some music",
    "set a timer for five minutes",
    "what's the weather forecast",
    "send a message to Brandon",
    "open the garage door",
    "how much does this cost",
    "can you help me please",
    "good morning everyone",
    "good night sleep well",
    "the quick brown fox jumps over the lazy dog",
    "thank you very much",
    "where is the nearest store",
    "call mom on the phone",
    "remind me to buy groceries",
    "what's on the calendar",
    "read me the news headlines",
    "start the coffee machine",
    "is it going to rain",
    "what's for dinner tonight",
    "I need to leave in ten minutes",
    "schedule a meeting for tomorrow",
    "play the next song",
    "volume up please",
    "volume down please",
    "stop the music",
    "pause the video",
    "resume playback",
    # Adversarial: contain fragments of the wake phrase
    "the black cat ran across the street",
    "I need a bigger box please",
    "tape recorder from the nineties",
    "voice recorder is running",
    "flight to New York",
    "black coffee with sugar",
    "box of chocolates",
    "data recorder on the dashboard",
    "flight attendant please",
    "the black hole in space",
    "can you record this call",
    "put the box on the shelf",
    "flight cancelled today",
    "record the meeting",
    "black Friday sale",
    "passenger flight delayed",
    "empty box in the corner",
    "record my voice memo",
    "black tie event",
    "cardboard box factory",
    "flight simulator game",
    "record the temperature",
    "black ink pen",
    "box office hit",
    "night flight over the ocean",
    "record store downtown",
    # Rhyming / near-phonetic
    "back dog night protector",
    "stack jocks light collector",
    "track box flight protector",
    "lack mocks might perfector",
    # Long sentences
    "the weather forecast today indicates partly cloudy skies with a chance of rain in the evening",
    "please remind me to pick up the dry cleaning on my way home from work tomorrow afternoon",
    "the quarterly earnings report will be released next Tuesday at three o'clock Eastern time",
    "I was thinking we could go for a walk in the park after dinner if you're not too tired",
    "the robot is navigating through the living room toward the kitchen right now",
    "can you tell me more about the new features in the latest software update",
    "it seems like the internet connection is really slow this morning",
    "please summarize the key points from the article I sent you earlier",
    "the quick brown fox jumped over the lazy dogs tail in the back yard",
    "let's talk about the plan for the weekend trip to the mountains",
]

VOICES = [
    "en_US-amy-medium",
    "en_US-lessac-medium",
    "en_US-ryan-high",
    "en_US-kathleen-low",
    "en_US-joe-medium",
    "en_GB-alan-medium",
    "en_GB-jenny_dioco-medium",
]

LENGTH_SCALES = [0.85, 1.0, 1.15, 1.3]
NOISE_SCALES = [0.5, 0.667, 0.8]
NOISE_W_SCALES = [0.6, 0.8, 1.0]


def synth_one(piper_bin, voice_model, phrase, length_scale, noise_scale, noise_w, out_wav):
    cmd = [
        piper_bin,
        "-m", voice_model,
        "--length-scale", str(length_scale),
        "--noise-scale", str(noise_scale),
        "--noise-w-scale", str(noise_w),
        "-f", str(out_wav),
    ]
    try:
        proc = subprocess.run(cmd, input=phrase, capture_output=True, text=True, timeout=120)
        return proc.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 1000
    except Exception:
        return False


def post_process(wav_path, target_sr=16000, target_dur=3.5, rng=None):
    audio, sr = sf.read(wav_path)
    if audio.ndim > 1:
        audio = audio[:, 0]
    audio = audio.astype(np.float32)
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak * 0.9
    if sr != target_sr:
        audio = resample_poly(audio, target_sr, sr).astype(np.float32)
        sr = target_sr

    total_samples = int(target_dur * sr)
    if len(audio) >= total_samples:
        # Random slice within the clip
        start = 0 if len(audio) == total_samples else rng.randint(0, len(audio) - total_samples)
        audio = audio[start:start + total_samples]
    else:
        pad_total = total_samples - len(audio)
        pad_before = rng.randint(0, pad_total) if rng else 0
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
    ap.add_argument("--n-samples", type=int, default=800)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path("/tmp/oww_train_neg_raw")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Filter voices to those that exist
    voices = [v for v in VOICES if (Path(args.voices_dir) / f"{v}.onnx").exists()]
    assert voices, "No voices found"

    count = 0
    for i in range(args.n_samples):
        phrase = rng.choice(GENERIC_PHRASES)
        voice = rng.choice(voices)
        voice_model = str(Path(args.voices_dir) / f"{voice}.onnx")
        length_scale = rng.choice(LENGTH_SCALES)
        noise = rng.choice(NOISE_SCALES)
        noise_w = rng.choice(NOISE_W_SCALES)
        raw = tmp_dir / f"neg_{i:05d}.wav"
        if not synth_one(args.piper_bin, voice_model, phrase, length_scale, noise, noise_w, raw):
            continue
        try:
            audio = post_process(raw, rng=rng)
            out_wav = out_dir / f"neg_{i:05d}.wav"
            sf.write(out_wav, audio, 16000, subtype="PCM_16")
            count += 1
            if count % 50 == 0:
                print(f"  neg samples: {count}")
        except Exception as e:
            print(f"post err: {e}")
        finally:
            raw.unlink(missing_ok=True)

    # Add pure silence / low-noise samples (important for rejection of background)
    for i in range(50):
        noise_level = rng.uniform(0, 0.02)
        samples = (np.random.randn(40000) * noise_level).astype(np.float32)
        sf.write(out_dir / f"neg_silence_{i:03d}.wav", samples, 16000, subtype="PCM_16")
        count += 1

    print(f"[done] total negative clips: {count}")


if __name__ == "__main__":
    main()
