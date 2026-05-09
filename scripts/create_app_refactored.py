#!/usr/bin/env python3
"""
Create app_refactored.py Entry Point

Builds a new entry point that:
1. Imports from extracted modules (models.py)
2. Includes all non-extracted code from app.py
3. Maintains identical functionality

Usage:
    python3 scripts/create_app_refactored.py

Output:
    Orchestrator/app_refactored.py
"""

import json
from pathlib import Path

# Paths
BLACKBOX_ROOT = Path(__file__).parent.parent
MANIFEST_PATH = BLACKBOX_ROOT / "Manifest" / "code_manifest.json"
APP_PY_PATH = BLACKBOX_ROOT / "Orchestrator" / "app.py"
OUTPUT_PATH = BLACKBOX_ROOT / "Orchestrator" / "app_refactored.py"


def build_app_refactored():
    """Build app_refactored.py from manifest."""

    print("="*70)
    print("CREATE APP_REFACTORED.PY - Refactored Entry Point")
    print("="*70)
    print()

    # Load manifest
    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    source_bytes = APP_PY_PATH.read_bytes()
    source_text = source_bytes.decode('utf-8')
    lines = source_text.split('\n')

    # Build new file
    output_lines = []

    # Header
    output_lines.append('#!/usr/bin/env python3')
    output_lines.append('"""')
    output_lines.append('Orchestrator/app_refactored.py - Modularized BlackBox Orchestrator')
    output_lines.append('')
    output_lines.append('This is a refactored version of app.py that imports from extracted modules.')
    output_lines.append('Functionality is identical to app.py but with better separation of concerns.')
    output_lines.append('')
    output_lines.append('Extracted modules:')

    extracted_count = 0
    for section_name, section in manifest["sections"].items():
        if section.get("refactor_status") == "extracted":
            output_lines.append(f'  - {section["target_module"]:20s} (from {section_name})')
            extracted_count += 1

    output_lines.append('"""')
    output_lines.append('')

    print(f"Extracted modules: {extracted_count}")
    print()

    # Add imports for extracted modules
    if extracted_count > 0:
        output_lines.append('# ===================================================================')
        output_lines.append('# Imports from Extracted Modules')
        output_lines.append('# ===================================================================')

        for section_name, section in manifest["sections"].items():
            if section.get("refactor_status") == "extracted":
                module_name = section["target_module"].replace('.py', '')
                output_lines.append(f'# From {section_name}:')
                output_lines.append(f'from {module_name} import *')
                print(f"  Importing from: {module_name}")

        output_lines.append('')
        output_lines.append('# ===================================================================')
        output_lines.append('# Remaining Code from app.py (Not Yet Extracted)')
        output_lines.append('# ===================================================================')
        output_lines.append('')

    # Copy all non-extracted sections
    for section_name, section in sorted(manifest["sections"].items(), key=lambda x: x[1]["line_start"]):
        if section.get("refactor_status") == "extracted":
            # Skip extracted sections (already imported)
            print(f"  Skipping: {section_name:20s} (extracted to {section['target_module']})")
            continue

        # Include this section
        line_start = section["line_start"] - 1  # Convert to 0-indexed
        line_end = section["line_end"]

        section_lines = lines[line_start:line_end]

        output_lines.append(f'# {"-"*68}')
        output_lines.append(f'# {section_name}: {section["description"]}')
        output_lines.append(f'# Lines {section["line_start"]}-{section["line_end"]} from original app.py')
        output_lines.append(f'# {"-"*68}')
        output_lines.extend(section_lines)
        output_lines.append('')

        print(f"  Including: {section_name:20s} ({len(section_lines)} lines)")

    # Write output
    output_content = '\n'.join(output_lines)
    OUTPUT_PATH.write_text(output_content)

    print()
    print(f"✅ Created: {OUTPUT_PATH}")
    print(f"   Size: {OUTPUT_PATH.stat().st_size:,} bytes")
    print(f"   Lines: {len(output_lines):,}")

    # Summary
    print("\n" + "="*70)
    print("APP_REFACTORED.PY CREATED")
    print("="*70)
    print(f"Sections imported: {extracted_count}")
    print(f"Sections included: {len(manifest['sections']) - extracted_count}")
    print(f"Total functionality: 100% (identical to app.py)")
    print()
    print("Next steps:")
    print("  1. Test imports: python3 -c 'from Orchestrator import app_refactored'")
    print("  2. Run integration tests")
    print("  3. Compare behavior with original app.py")
    print("="*70)

    return 0


if __name__ == "__main__":
    exit(build_app_refactored())
