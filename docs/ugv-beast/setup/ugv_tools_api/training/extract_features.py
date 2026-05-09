#!/usr/bin/env python3
"""Extract (n_frames, 16, 96) embedding features from WAV clips using openwakeword's
pre-trained melspectrogram + speech_embedding ONNX models.

For each clip, we compute embeddings via a sliding window over the full audio.
Each (16, 96) window becomes ONE training example. This gives us many examples
per clip and aligns with how the wake-word model will be run at inference time.
"""
import argparse
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from openwakeword.utils import AudioFeatures


def extract_windows_from_clip(af: AudioFeatures, audio_int16: np.ndarray,
                              input_frames: int = 16, step: int = 4) -> np.ndarray:
    """Returns (n_windows, 16, 96) arrays of embedding features over the clip."""
    # _get_embeddings returns (n_frames, 96) features
    emb = af._get_embeddings(audio_int16)
    if emb.ndim == 1:
        emb = emb.reshape(-1, 96)
    if len(emb) < input_frames:
        return np.zeros((0, input_frames, 96), dtype=np.float32)
    windows = []
    for i in range(0, len(emb) - input_frames + 1, step):
        windows.append(emb[i:i + input_frames])
    return np.stack(windows).astype(np.float32)


def process_dir(af, in_dir: Path, label: int, step: int) -> tuple:
    """Process all wav files in a directory. Returns (features, labels)."""
    files = sorted(in_dir.glob("*.wav"))
    if not files:
        return np.zeros((0, 16, 96), dtype=np.float32), np.zeros(0, dtype=np.int64)

    all_windows = []
    for idx, f in enumerate(files):
        try:
            audio, sr = sf.read(f)
            if sr != 16000:
                print(f"[skip] wrong sr {sr}: {f.name}")
                continue
            if audio.ndim > 1:
                audio = audio[:, 0]
            audio_int16 = (audio * 32767).astype(np.int16)
            w = extract_windows_from_clip(af, audio_int16, step=step)
            if len(w):
                all_windows.append(w)
        except Exception as e:
            print(f"[err] {f.name}: {e}")
        if (idx + 1) % 100 == 0:
            print(f"  {label}: processed {idx+1}/{len(files)}, total windows: {sum(len(x) for x in all_windows)}")
    if not all_windows:
        return np.zeros((0, 16, 96), dtype=np.float32), np.zeros(0, dtype=np.int64)
    features = np.concatenate(all_windows, axis=0)
    labels = np.full(len(features), label, dtype=np.int64)
    return features, labels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pos-dir", required=True)
    ap.add_argument("--neg-dir", required=True)
    ap.add_argument("--out", required=True, help=".npz output")
    ap.add_argument("--pos-step", type=int, default=1,
                    help="Stride for positive windows (smaller = more examples)")
    ap.add_argument("--neg-step", type=int, default=4,
                    help="Stride for negative windows")
    args = ap.parse_args()

    print("[*] loading feature extractor...")
    af = AudioFeatures(ncpu=4)
    print("[*] extracting positives...")
    X_pos, y_pos = process_dir(af, Path(args.pos_dir), label=1, step=args.pos_step)
    print(f"    positives: {X_pos.shape}")
    print("[*] extracting negatives...")
    X_neg, y_neg = process_dir(af, Path(args.neg_dir), label=0, step=args.neg_step)
    print(f"    negatives: {X_neg.shape}")

    X = np.concatenate([X_pos, X_neg], axis=0)
    y = np.concatenate([y_pos, y_neg], axis=0)
    print(f"[*] combined: {X.shape}, pos ratio: {y.mean():.3f}")
    np.savez_compressed(args.out, X=X, y=y)
    print(f"[done] saved {args.out}")


if __name__ == "__main__":
    main()
