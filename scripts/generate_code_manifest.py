#!/usr/bin/env python3
"""
Generate Byte-Offset Manifest for Orchestrator/app.py

This script creates an immutable manifest tracking every section of app.py using:
- Byte offsets (for precise extraction)
- SHA-256 hashes (for integrity verification)
- Line ranges (for human readability)

The manifest enables read-only refactoring where app.py is never modified,
only extracted from using byte-offset addressing.

Usage:
    python3 scripts/generate_code_manifest.py

Output:
    Manifest/code_manifest.json
"""

import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Tuple

# Paths
BLACKBOX_ROOT = Path(__file__).parent.parent
APP_PY_PATH = BLACKBOX_ROOT / "Orchestrator" / "app.py"
MANIFEST_DIR = BLACKBOX_ROOT / "Manifest"
OUTPUT_PATH = MANIFEST_DIR / "code_manifest.json"

# Section definitions (from agent analysis of app.py structure)
# Format: (name, line_start, line_end, description, target_module)
SECTIONS: List[Tuple[str, int, int, str, str]] = [
    ("IMPORTS", 1, 286, "All imports, configuration loading, API keys, global constants", "config.py"),
    ("OPERATOR_STATE", 287, 503, "OpState management, preferences, app registry persistence", "state.py"),
    ("TASK_MODELS", 504, 730, "AgentSession, Task, TaskDatabase, TaskType enums", "models.py"),
    ("BACKGROUND_WORKER", 731, 1758, "Task processing loop, media processors, chat workflow", "tasks/"),
    ("UTILITIES", 1759, 2011, "Time, hashing, file I/O, volume management, archiving", "volume.py"),
    ("ARTIFACTS", 2012, 2192, "PDF/CSV/DOCX generation, artifact parsing", "artifacts.py"),
    ("MONITORING", 2193, 2584, "System metrics, cleanup, drift visualization", "monitoring.py"),
    ("FOSSIL_RETRIEVAL", 2585, 3409, "Semantic search, keyword search, indexing, embeddings", "fossils/"),
    ("CHECKPOINT", 3410, 3701, "Checkpoint creation, snapshot minting, perform_mint()", "checkpoint.py"),
    ("STARTUP", 3702, 3845, "Signal handlers, startup checks, Pydantic models", "startup.py"),
    ("ROUTES_TASKS", 3846, 3993, "Task listing, status, fossil search endpoints", "routes/task_routes.py"),
    ("ROUTES_CORE", 3994, 4567, "Health, upload, dashboard, admin endpoints", "routes/admin_routes.py"),
    ("ROUTES_TTS", 4568, 5455, "OpenAI TTS, Google TTS, Gemini TTS, Lyria music", "media/tts/"),
    ("ROUTES_LLM", 5456, 6698, "Chat streaming, LLM providers, context building", "routes/chat_routes.py"),
    ("ROUTES_AGENT", 6699, 7877, "Claude Code agent, WebSocket, session management", "routes/agent_routes.py"),
]


def calculate_byte_offset(lines: List[str], line_number: int) -> int:
    """
    Calculate the byte offset for a given line number.

    Args:
        lines: All lines from the file
        line_number: Target line number (1-indexed)

    Returns:
        Byte offset where that line starts
    """
    # Get all text before the target line
    text_before = '\n'.join(lines[:line_number - 1])

    # Convert to bytes
    byte_offset = len(text_before.encode('utf-8'))

    # Add 1 for the newline after previous line (unless it's line 1)
    if text_before:
        byte_offset += 1

    return byte_offset


def generate_section_hash(source_bytes: bytes, byte_start: int, byte_end: int) -> str:
    """Generate SHA-256 hash for a section."""
    section_bytes = source_bytes[byte_start:byte_end]
    return hashlib.sha256(section_bytes).hexdigest()


def generate_manifest() -> Dict:
    """Generate complete byte-offset manifest for app.py."""

    print(f"Reading source file: {APP_PY_PATH}")

    # Read as bytes (critical for byte-offset accuracy)
    source_bytes = APP_PY_PATH.read_bytes()
    source_text = source_bytes.decode('utf-8')
    lines = source_text.split('\n')

    print(f"File size: {len(source_bytes):,} bytes")
    print(f"Total lines: {len(lines):,}")

    # Calculate overall file hash
    file_hash = hashlib.sha256(source_bytes).hexdigest()
    print(f"File SHA-256: {file_hash}")

    # Initialize manifest
    manifest = {
        "source_file": str(APP_PY_PATH.relative_to(BLACKBOX_ROOT)),
        "total_bytes": len(source_bytes),
        "total_lines": len(lines),
        "sha256": file_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
        "refactoring_approach": "side-by-side",
        "frozen": True,  # Indicates app.py is read-only
        "sections": {}
    }

    print(f"\nProcessing {len(SECTIONS)} sections...\n")

    # Process each section
    for name, line_start, line_end, description, target_module in SECTIONS:
        print(f"Processing {name:20s} (lines {line_start:5d}-{line_end:5d})...")

        # Calculate byte offsets
        byte_start = calculate_byte_offset(lines, line_start)
        byte_end = calculate_byte_offset(lines, line_end + 1)  # +1 to include last line

        # Generate section hash
        section_hash = generate_section_hash(source_bytes, byte_start, byte_end)

        # Store in manifest
        manifest["sections"][name] = {
            "byte_start": byte_start,
            "byte_end": byte_end,
            "line_start": line_start,
            "line_end": line_end,
            "sha256": section_hash,
            "description": description,
            "target_module": target_module,
            "refactor_status": "pending",
            "dependencies": [],  # Will be filled in Day 2
            "exports": [],  # Will be filled in Day 2
            "embedding": None  # Will be generated in Day 2
        }

        section_size = byte_end - byte_start
        print(f"  → {section_size:,} bytes, hash: {section_hash[:16]}...")

    return manifest


def verify_manifest(manifest: Dict, source_file: Path) -> bool:
    """Verify all SHA-256 hashes in manifest match actual file."""

    print("\n" + "="*70)
    print("VERIFICATION: Checking manifest integrity...")
    print("="*70)

    source_bytes = source_file.read_bytes()

    # Verify overall file hash
    actual_file_hash = hashlib.sha256(source_bytes).hexdigest()
    expected_file_hash = manifest["sha256"]

    if actual_file_hash != expected_file_hash:
        print(f"❌ FAILED: File hash mismatch!")
        print(f"   Expected: {expected_file_hash}")
        print(f"   Actual:   {actual_file_hash}")
        return False

    print(f"✅ File hash verified: {actual_file_hash[:16]}...")

    # Verify each section
    errors = 0
    for name, section in manifest["sections"].items():
        start = section["byte_start"]
        end = section["byte_end"]

        section_bytes = source_bytes[start:end]
        actual_hash = hashlib.sha256(section_bytes).hexdigest()
        expected_hash = section["sha256"]

        if actual_hash != expected_hash:
            print(f"❌ Section {name}: Hash mismatch!")
            errors += 1
        else:
            print(f"✅ Section {name:20s}: Verified ({end - start:,} bytes)")

    if errors > 0:
        print(f"\n❌ VERIFICATION FAILED: {errors} section(s) have hash mismatches")
        return False

    print(f"\n✅ ALL SECTIONS VERIFIED: Manifest is accurate")
    return True


def save_manifest(manifest: Dict, output_path: Path):
    """Save manifest to JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w') as f:
        json.dump(manifest, f, indent=2)

    print(f"\n✅ Manifest saved to: {output_path}")
    print(f"   Size: {output_path.stat().st_size:,} bytes")


def main():
    print("="*70)
    print("CODE MANIFEST GENERATOR - BlackBox Refactoring System")
    print("="*70)
    print()

    # Generate manifest
    manifest = generate_manifest()

    # Verify integrity
    if not verify_manifest(manifest, APP_PY_PATH):
        print("\n❌ FATAL: Manifest verification failed. Aborting.")
        return 1

    # Save to file
    save_manifest(manifest, OUTPUT_PATH)

    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print(f"Source file: {manifest['source_file']}")
    print(f"Total size: {manifest['total_bytes']:,} bytes ({manifest['total_lines']:,} lines)")
    print(f"Sections defined: {len(manifest['sections'])}")
    print(f"File hash: {manifest['sha256']}")
    print(f"Frozen (read-only): {manifest['frozen']}")
    print()
    print("✅ Code manifest generation complete!")
    print()
    print("Next steps:")
    print("  1. Review: Manifest/code_manifest.json")
    print("  2. Day 2: Generate embeddings for semantic search")
    print("  3. Day 3: Build integration test harness")
    print("="*70)

    return 0


if __name__ == "__main__":
    exit(main())
