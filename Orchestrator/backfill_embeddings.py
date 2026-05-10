#!/usr/bin/env python3
"""
Backfill / upgrade embeddings for existing snapshots.

This script reads all snapshots from the volume file, generates embeddings
for those that don't have them, and updates the snapshot index.

Usage:
    python3 Orchestrator/backfill_embeddings.py             # Only fill missing embeddings
    python3 Orchestrator/backfill_embeddings.py --upgrade    # Regenerate old 768-dim embeddings too
"""

import os
import json
import time
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
import google.generativeai as genai

EXPECTED_DIMS = 3072  # gemini-embedding-001 output dimensions

# Load environment from .env (must happen before importing central config)
load_dotenv()
from Orchestrator.config import GOOGLE_API_KEY

if not GOOGLE_API_KEY:
    print("[ERROR] GOOGLE_API_KEY not found in .env file!")
    sys.exit(1)

# Configure Gemini
genai.configure(api_key=GOOGLE_API_KEY)

# Paths (relative to project root where script is run from)
SNAPSHOT_INDEX = Path("Manifest/snapshot_index.json")
VOL_PATH = Path("Volumes/SNAPSHOT_VOLUME.txt")

def load_snapshot_index() -> Dict[str, Dict[str, Any]]:
    """Load the snapshot index from disk."""
    try:
        if SNAPSHOT_INDEX.exists():
            with open(SNAPSHOT_INDEX, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to load snapshot index: {e}")
        return {}
    return {}

def save_snapshot_index(index: Dict[str, Dict[str, Any]]):
    """Save the snapshot index to disk atomically."""
    try:
        SNAPSHOT_INDEX.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = SNAPSHOT_INDEX.with_suffix('.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(index, f, indent=2)
        os.replace(tmp_path, SNAPSHOT_INDEX)
    except Exception as e:
        print(f"[ERROR] Failed to save snapshot index: {e}")

def generate_embedding(text: str, max_retries: int = 3) -> Optional[List[float]]:
    """Generate embedding for text using Gemini Embedding API."""
    # Truncate text if too long (Gemini has token limits)
    max_chars = 10000
    if len(text) > max_chars:
        text = text[:max_chars] + "..."

    for attempt in range(max_retries):
        try:
            result = genai.embed_content(
                model="models/gemini-embedding-001",
                content=text,
                task_type="retrieval_document"
            )
            return result['embedding']
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                print(f"   ⚠️  Retry {attempt + 1}/{max_retries} after {wait_time}s (error: {e})")
                time.sleep(wait_time)
            else:
                print(f"   ❌ Failed after {max_retries} attempts: {e}")
                return None

def read_volume_bytes() -> bytes:
    """Read the volume file as bytes."""
    try:
        with open(VOL_PATH, 'rb') as f:
            return f.read()
    except Exception as e:
        print(f"[ERROR] Failed to read volume: {e}")
        return b""

def main():
    upgrade_mode = "--upgrade" in sys.argv

    print("=" * 70)
    if upgrade_mode:
        print(f"SNAPSHOT EMBEDDING UPGRADE (target: {EXPECTED_DIMS}-dim)")
    else:
        print("SNAPSHOT EMBEDDING BACKFILL")
    print("=" * 70)
    print()

    # Load index
    print("[1/5] Loading snapshot index...")
    index = load_snapshot_index()
    total_snapshots = len(index)
    print(f"   ✓ Loaded {total_snapshots} snapshots")
    print()

    # Count snapshots needing embeddings
    print("[2/5] Checking which snapshots need embeddings...")
    snapshots_needing_embeddings = []
    snapshots_current = 0
    snapshots_old_dims = 0
    snapshots_missing = 0

    for snap_id, meta in index.items():
        emb = meta.get("embedding")
        if emb and isinstance(emb, list) and len(emb) > 0:
            if len(emb) == EXPECTED_DIMS:
                snapshots_current += 1
            else:
                snapshots_old_dims += 1
                if upgrade_mode:
                    snapshots_needing_embeddings.append(snap_id)
        else:
            snapshots_missing += 1
            snapshots_needing_embeddings.append(snap_id)

    print(f"   ✓ {snapshots_current} snapshots already at {EXPECTED_DIMS}-dim (current)")
    if snapshots_old_dims > 0:
        print(f"   ⚠️  {snapshots_old_dims} snapshots have old-dimension embeddings")
    if snapshots_missing > 0:
        print(f"   ❌ {snapshots_missing} snapshots have no embeddings")
    print(f"   ⚡ {len(snapshots_needing_embeddings)} snapshots to process")
    if not upgrade_mode and snapshots_old_dims > 0:
        print(f"   💡 Run with --upgrade to regenerate the {snapshots_old_dims} old-dimension embeddings")
    print()

    if len(snapshots_needing_embeddings) == 0:
        print("🎉 All snapshots already have current embeddings! Nothing to do.")
        return

    # Load volume
    print("[3/5] Loading volume file...")
    vol_bytes = read_volume_bytes()
    print(f"   ✓ Loaded {len(vol_bytes):,} bytes")
    print()

    # Estimate cost and time
    avg_tokens_per_snapshot = 2500
    total_tokens = len(snapshots_needing_embeddings) * avg_tokens_per_snapshot
    estimated_cost = (total_tokens / 1000) * 0.00001
    estimated_time_minutes = len(snapshots_needing_embeddings) * 1 / 60

    print("[4/5] Estimated resources:")
    print(f"   💰 Cost: ~${estimated_cost:.4f} ({total_tokens:,} tokens)")
    print(f"   ⏱️  Time: ~{estimated_time_minutes:.1f} minutes")
    print()

    # Process embeddings
    print("[5/5] Generating embeddings...")
    print()

    success_count = 0
    failure_count = 0
    start_time = time.time()

    for i, snap_id in enumerate(snapshots_needing_embeddings, 1):
        meta = index[snap_id]

        # Extract snapshot text using byte offsets
        byte_start = meta.get("byte_start", 0)
        byte_end = meta.get("byte_end", 0)

        if byte_start >= len(vol_bytes) or byte_end > len(vol_bytes):
            print(f"   [{i}/{len(snapshots_needing_embeddings)}] {snap_id} - ❌ Invalid byte range")
            failure_count += 1
            continue

        snap_bytes = vol_bytes[byte_start:byte_end]
        snap_text = snap_bytes.decode('utf-8', errors='replace')

        # Generate embedding
        print(f"   [{i}/{len(snapshots_needing_embeddings)}] {snap_id}...", end=" ", flush=True)

        embedding = generate_embedding(snap_text)

        if embedding:
            # Update index
            index[snap_id]["embedding"] = embedding
            success_count += 1
            print(f"✓ ({len(embedding)} dims)")

            # Save progress every 10 snapshots
            if i % 10 == 0:
                save_snapshot_index(index)
                elapsed = time.time() - start_time
                rate = i / elapsed
                remaining = len(snapshots_needing_embeddings) - i
                eta_seconds = remaining / rate if rate > 0 else 0
                print(f"      💾 Progress saved ({i}/{len(snapshots_needing_embeddings)}, ETA: {eta_seconds/60:.1f}m)")
        else:
            failure_count += 1

        # Small delay to avoid rate limiting
        time.sleep(0.2)

    # Final save
    print()
    print("   💾 Saving final index...")
    save_snapshot_index(index)

    # Summary
    elapsed_time = time.time() - start_time
    print()
    print("=" * 70)
    print("BACKFILL COMPLETE!")
    print("=" * 70)
    print(f"   ✓ Success: {success_count} snapshots")
    if failure_count > 0:
        print(f"   ❌ Failed: {failure_count} snapshots")
    print(f"   ⏱️  Time: {elapsed_time/60:.1f} minutes ({elapsed_time:.1f}s)")
    print(f"   📊 Rate: {success_count/elapsed_time:.1f} snapshots/second")
    print()
    print("🎉 Your entire snapshot history now has semantic embeddings!")
    print()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        print()
        print("⚠️  Interrupted by user. Progress has been saved.")
        print("   Run the script again to resume from where you left off.")
        sys.exit(0)
    except Exception as e:
        print()
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
