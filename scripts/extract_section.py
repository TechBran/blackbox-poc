#!/usr/bin/env python3
"""
Extract Section from app.py using Byte-Offset Manifest

This script extracts a section from app.py and creates a new module file
while preserving app.py as frozen/read-only.

Usage:
    python3 scripts/extract_section.py TASK_MODELS

Output:
    Orchestrator/models.py (extracted code)
    Manifest/code_manifest.json (updated with extraction status)
"""

import hashlib
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

# Paths
BLACKBOX_ROOT = Path(__file__).parent.parent
MANIFEST_PATH = BLACKBOX_ROOT / "Manifest" / "code_manifest.json"
DEPENDENCIES_PATH = BLACKBOX_ROOT / "Manifest" / "code_dependencies.json"
APP_PY_PATH = BLACKBOX_ROOT / "Orchestrator" / "app.py"


def verify_integrity_before_extraction(manifest: dict) -> bool:
    """Verify app.py hasn't been modified since manifest creation."""
    print("Verifying app.py integrity...")

    source_bytes = APP_PY_PATH.read_bytes()
    actual_hash = hashlib.sha256(source_bytes).hexdigest()
    expected_hash = manifest["sha256"]

    if actual_hash != expected_hash:
        print(f"❌ FATAL: app.py has been modified!")
        print(f"   Expected: {expected_hash}")
        print(f"   Actual:   {actual_hash}")
        print(f"\n⚠️  Cannot proceed with extraction.")
        return False

    print(f"✅ app.py unchanged (hash: {actual_hash[:16]}...)")
    return True


def extract_section(section_name: str, manifest: dict, dependencies: dict) -> str:
    """Extract section code using byte offsets."""
    section = manifest["sections"][section_name]

    print(f"\nExtracting {section_name}...")
    print(f"  Lines: {section['line_start']}-{section['line_end']}")
    print(f"  Bytes: {section['byte_start']}-{section['byte_end']}")

    # Read source as bytes
    source_bytes = APP_PY_PATH.read_bytes()

    # Extract using byte offsets
    section_bytes = source_bytes[section["byte_start"]:section["byte_end"]]
    section_code = section_bytes.decode('utf-8')

    # Verify extracted hash matches
    actual_hash = hashlib.sha256(section_bytes).hexdigest()
    expected_hash = section["sha256"]

    if actual_hash != expected_hash:
        print(f"❌ ERROR: Section hash mismatch!")
        raise ValueError(f"Extraction verification failed for {section_name}")

    print(f"  ✅ Extracted {len(section_code)} chars, hash verified")

    return section_code


def generate_imports(section_name: str, dependencies: dict) -> str:
    """Generate necessary import statements for extracted section."""
    deps = dependencies[section_name]

    imports = []

    # Standard library imports
    if deps["imports_stdlib"]:
        imports.append("# Standard library imports")
        for mod in sorted(deps["imports_stdlib"]):
            imports.append(f"import {mod}")

    # External library imports
    if deps["imports_external"]:
        if imports:
            imports.append("")
        imports.append("# External library imports")
        for mod in sorted(deps["imports_external"]):
            imports.append(f"import {mod}")

    # Local imports (from other extracted modules)
    if deps["imports_local"]:
        if imports:
            imports.append("")
        imports.append("# Local imports")
        for mod in sorted(deps["imports_local"]):
            imports.append(f"from {mod} import *")

    return '\n'.join(imports) if imports else "# No imports needed"


def create_module_file(section_name: str, code: str, imports: str, target_path: Path, section_info: dict):
    """Create the new module file."""
    header = f'''#!/usr/bin/env python3
"""
{target_path.name} - Extracted from Orchestrator/app.py

This module was automatically extracted using byte-offset manifest refactoring.
Original location: Lines {section_info["line_start"]}-{section_info["line_end"]}

Extraction date: {datetime.now(timezone.utc).isoformat()}
Original SHA-256: {section_info["sha256"]}
"""

'''

    # Combine header + imports + code
    full_content = header + imports + "\n\n" + code

    # Write to file
    target_path.write_text(full_content)

    print(f"\n✅ Created: {target_path}")
    print(f"   Size: {target_path.stat().st_size:,} bytes")

    return full_content


def update_manifest_after_extraction(manifest: dict, section_name: str, target_module: str):
    """Update manifest to mark section as extracted."""
    manifest["sections"][section_name]["refactor_status"] = "extracted"
    manifest["sections"][section_name]["extracted_to"] = target_module
    manifest["sections"][section_name]["extracted_at"] = datetime.now(timezone.utc).isoformat()

    # Save updated manifest
    with open(MANIFEST_PATH, 'w') as f:
        json.dump(manifest, f, indent=2)

    print(f"\n✅ Manifest updated: {section_name} marked as extracted")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/extract_section.py SECTION_NAME")
        print("\nAvailable sections:")
        with open(MANIFEST_PATH) as f:
            manifest = json.load(f)
        for name in manifest["sections"].keys():
            print(f"  - {name}")
        return 1

    section_name = sys.argv[1]

    print("="*70)
    print(f"EXTRACT SECTION: {section_name}")
    print("="*70)
    print()

    # Load manifest and dependencies
    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    with open(DEPENDENCIES_PATH) as f:
        dependencies = json.load(f)

    if section_name not in manifest["sections"]:
        print(f"❌ ERROR: Section '{section_name}' not found in manifest")
        return 1

    section_info = manifest["sections"][section_name]

    # Check if already extracted
    if section_info.get("refactor_status") == "extracted":
        print(f"⚠️  Section already extracted to: {section_info.get('extracted_to')}")
        print(f"   Extraction date: {section_info.get('extracted_at')}")
        return 0

    # Verify integrity
    if not verify_integrity_before_extraction(manifest):
        return 1

    # Extract section
    code = extract_section(section_name, manifest, dependencies)

    # Generate imports
    imports = generate_imports(section_name, dependencies)
    print(f"\nGenerated imports:")
    print(f"  {imports.count('import')} import statements")

    # Determine target file path
    target_module = section_info["target_module"]
    target_path = BLACKBOX_ROOT / "Orchestrator" / target_module

    # Create module file
    create_module_file(section_name, code, imports, target_path, section_info)

    # Update manifest
    update_manifest_after_extraction(manifest, section_name, target_module)

    print("\n" + "="*70)
    print(f"✅ EXTRACTION COMPLETE: {section_name} → {target_module}")
    print("="*70)
    print(f"\nNext steps:")
    print(f"  1. Review: {target_path}")
    print(f"  2. Test: Import from other modules")
    print(f"  3. Create: app_refactored.py that uses this module")
    print(f"  4. Verify: Integration tests still pass")

    return 0


if __name__ == "__main__":
    exit(main())
