#!/usr/bin/env python3
"""Verify the trained 'black box flight recorder' wake-word model.

Synthesizes one positive phrase + one negative phrase via Piper TTS,
runs the model frame-by-frame (80ms chunks = 1280 samples at 16kHz),
and asserts:
  - positive clip peaks above 0.5 (wake detected)
  - negative clip stays below 0.3 throughout (no false positive)

Usage:
    python3 verify_wakeword.py \
        --model /path/to/black_box_flight_recorder.onnx \
        --piper-bin /path/to/piper \
        --voice /path/to/en_US-amy-medium.onnx

Exits 0 on success, 1 on failure.
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly


def synth(piper_bin: str, voice: str, phrase: str, out_wav: Path) -> None:
    cmd = [piper_bin, "-m", voice, "-f", str(out_wav)]
    proc = subprocess.run(cmd, input=phrase, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"Piper failed: {proc.stderr[:200]}")


def load_16k_int16(wav_path: Path) -> np.ndarray:
    audio, sr = sf.read(wav_path)
    if audio.ndim > 1:
        audio = audio[:, 0]
    audio = audio.astype(np.float32)
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak * 0.9
    if sr != 16000:
        audio = resample_poly(audio, 16000, sr).astype(np.float32)
    return (audio * 32767).astype(np.int16)


def run_model(model_path: str, audio: np.ndarray, chunk_samples: int = 1280) -> list:
    """Feed audio through openwakeword.model.Model in 80ms chunks, return scores list."""
    from openwakeword.model import Model
    # openwakeword >= 0.6 renamed `wakeword_model_paths` to `wakeword_models`
    # and requires an explicit `inference_framework`.
    try:
        model = Model(wakeword_models=[model_path], inference_framework="onnx")
    except (TypeError, ValueError):
        model = Model(wakeword_model_paths=[model_path])
    scores = []
    # openwakeword expects int16 16kHz; it accumulates buffer internally.
    # predict() must be called with small chunks so the embedding window slides.
    # 80ms = 1280 samples per chunk is typical.
    for start in range(0, len(audio) - chunk_samples + 1, chunk_samples):
        chunk = audio[start:start + chunk_samples]
        preds = model.predict(chunk)
        # preds is dict[str, float]
        for k, v in preds.items():
            if k.endswith("black_box_flight_recorder") or "black_box" in k.lower():
                scores.append((start / 16000.0, float(v)))
                break
        else:
            # fallback: just take first value
            if preds:
                scores.append((start / 16000.0, float(list(preds.values())[0])))
    return scores


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--piper-bin", default="piper")
    ap.add_argument("--voice", required=True, help="Path to Piper voice .onnx")
    ap.add_argument("--pos-phrase",
                    default="Black Box Flight Recorder, what time is it?")
    ap.add_argument("--neg-phrase",
                    default="Hello Brandon, how are you today?")
    ap.add_argument("--pos-threshold", type=float, default=0.5)
    ap.add_argument("--neg-threshold", type=float, default=0.3)
    ap.add_argument("--work-dir", default=None)
    args = ap.parse_args()

    work = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="oww_verify_"))
    work.mkdir(parents=True, exist_ok=True)
    print(f"[*] work dir: {work}")

    # synth positive + negative
    pos_wav = work / "verify_pos.wav"
    neg_wav = work / "verify_neg.wav"
    print(f"[*] synthesizing positive: '{args.pos_phrase}'")
    synth(args.piper_bin, args.voice, args.pos_phrase, pos_wav)
    print(f"[*] synthesizing negative: '{args.neg_phrase}'")
    synth(args.piper_bin, args.voice, args.neg_phrase, neg_wav)

    pos_audio = load_16k_int16(pos_wav)
    neg_audio = load_16k_int16(neg_wav)
    print(f"    pos dur: {len(pos_audio)/16000:.2f}s, neg dur: {len(neg_audio)/16000:.2f}s")

    # run model
    print(f"[*] loading model: {args.model}")
    t0 = time.time()
    pos_scores = run_model(args.model, pos_audio)
    neg_scores = run_model(args.model, neg_audio)
    t1 = time.time()
    total_frames = len(pos_scores) + len(neg_scores)
    total_audio_s = (len(pos_audio) + len(neg_audio)) / 16000
    print(f"    inference: {t1-t0:.2f}s over {total_audio_s:.2f}s audio "
          f"({total_frames} frames → {(t1-t0)*1000/max(total_frames,1):.1f}ms/frame)")

    # analyze
    pos_peak = max(s for _, s in pos_scores) if pos_scores else 0.0
    pos_peak_t = max(pos_scores, key=lambda x: x[1])[0] if pos_scores else 0.0
    neg_peak = max(s for _, s in neg_scores) if neg_scores else 0.0
    neg_peak_t = max(neg_scores, key=lambda x: x[1])[0] if neg_scores else 0.0

    print(f"\n[RESULTS]")
    print(f"  positive clip: peak score = {pos_peak:.3f} at t={pos_peak_t:.2f}s")
    print(f"  negative clip: peak score = {neg_peak:.3f} at t={neg_peak_t:.2f}s")

    # Print full trace for debugging
    print("\n  positive trace:")
    for t, s in pos_scores:
        marker = " <-- WAKE" if s > args.pos_threshold else ""
        print(f"    t={t:.2f}s  score={s:.3f}{marker}")
    print("\n  negative trace:")
    for t, s in neg_scores:
        marker = " <-- FALSE POSITIVE" if s > args.neg_threshold else ""
        print(f"    t={t:.2f}s  score={s:.3f}{marker}")

    ok = True
    if pos_peak < args.pos_threshold:
        print(f"\n[FAIL] positive peak {pos_peak:.3f} < {args.pos_threshold}")
        ok = False
    else:
        print(f"\n[PASS] positive peak {pos_peak:.3f} ≥ {args.pos_threshold}")
    if neg_peak > args.neg_threshold:
        print(f"[FAIL] negative peak {neg_peak:.3f} > {args.neg_threshold}")
        ok = False
    else:
        print(f"[PASS] negative peak {neg_peak:.3f} ≤ {args.neg_threshold}")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
