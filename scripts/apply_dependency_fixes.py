#!/usr/bin/env python3
"""
Apply Dependency Fixes

Reads the dependency audit and applies proper imports to all modules.
Filters out false positives (loop variables, local names) and adds only real dependencies.

Usage:
    python3 scripts/apply_dependency_fixes.py
"""

import json
from pathlib import Path
from typing import Dict, List
import re

BLACKBOX_ROOT = Path(__file__).parent.parent
ORCHESTRATOR_DIR = BLACKBOX_ROOT / "Orchestrator"
AUDIT_FILE = BLACKBOX_ROOT / "Manifest" / "dependency_audit.json"

# Real cross-module dependencies (manually curated based on audit)
DEPENDENCY_FIXES = {
    'config': {
        # Config is the root - no dependencies
    },
    'models': {
        'Orchestrator.config': ['CFG'],
        'Orchestrator.volume': ['now_utc_iso'],
    },
    'state': {
        'Orchestrator.config': ['USERS_DEFAULT'],
        'Orchestrator.models': ['AgentSession', 'RegisteredApp'],
    },
    'volume': {
        'Orchestrator.config': ['ARC_DIR', 'GM_PATH', 'GM_HASH', 'MANIFEST', 'ARTIFACT_RETENTION_DAYS'],
    },
    'artifacts': {
        'Orchestrator.config': ['ARTIFACTS_DIR'],
    },
    'monitoring': {
        'Orchestrator.config': ['CFG', 'VOL_PATH', 'ARC_DIR', 'UPLOADS_DIR', 'CURRENT_OPERATOR', 'GOOGLE_API_KEY'],
        'Orchestrator.fossils': ['load_snapshot_index'],
    },
    'fossils': {
        'Orchestrator.config': ['CFG', 'INCLUDE_OTHERS'],
        'Orchestrator.volume': ['read_volume_bytes'],
    },
    'checkpoint': {
        'Orchestrator.config': ['CHECKPOINT_AUTO_CREATE_INTERVAL', 'CHECKPOINT_MIN_SNAPSHOTS', 'CHECKPOINT_TURNS_TO_COMPRESS'],
        'Orchestrator.volume': ['append_snapshot_text', 'archive_volume', 'next_snap_id_from_tail', 'now_utc_iso', 'parse_tail', 'verify_gm_or_halt'],
        'Orchestrator.fossils': ['get_recent_fossils_for_operator', 'update_snapshot_index'],
        'Orchestrator.monitoring': ['drift_state_for', 'generate_embedding', 'render_snapshot_body_v71'],
        'Orchestrator.state': ['get_state', 'save_operator_state'],
        'Orchestrator.routes.chat_routes': ['call_gemini'],
    },
    'startup': {
        'Orchestrator.monitoring': ['cleanup_stuck_tasks', 'get_disk_usage'],
    },
    'tasks': {
        'Orchestrator.config': ['ANTHROPIC_MODEL_DEFAULT', 'DEFAULT_PROVIDER', 'GEMINI_MODEL_DEFAULT', 'GOOGLE_IMAGEN_MODEL', 'GOOGLE_VEO_MODEL', 'OPENAI_MODEL_DEFAULT', 'OUTPUT_SPEC', 'TOKENS_THRESHOLD', 'TURNS_THRESHOLD', 'AUTO_ENABLE', 'DEBOUNCE_MS', 'ON_YELLOW', 'ON_RED', 'GOOGLE_API_KEY'],
        'Orchestrator.volume': ['now_utc_iso'],
        'Orchestrator.fossils': ['get_recent_fossils_for_operator', 'get_recent_checkpoints_for_operator', 'hybrid_retrieve', 'extract_snap_ids'],
        'Orchestrator.monitoring': ['drift_state_for', 'extract_plan'],
        'Orchestrator.state': ['get_state', 'save_operator_state'],
        'Orchestrator.checkpoint': ['perform_mint', 'should_create_checkpoint', 'create_checkpoint_async'],
        'Orchestrator.artifacts': ['parse_and_process_artifacts'],
        'Orchestrator.routes.chat_routes': ['call_anthropic', 'call_gemini', 'call_openai'],
        'Orchestrator.routes.tts_routes': ['call_imagen', 'call_lyria_music', 'call_gemini_tts', 'call_google_tts_synthesize'],
        'Orchestrator.startup': ['ChatIn', 'GoogleSSMLIn', 'GeminiProTTSIn', 'LyriaMusicIn'],
    },
    'routes.task_routes': {
        'Orchestrator.config': ['VOL_PATH'],
        'Orchestrator.models': ['TaskType'],
        'Orchestrator.volume': ['read_volume_bytes'],
        'Orchestrator.fossils': ['load_snapshot_index'],
        'Orchestrator.tasks': ['create_task'],
        'Orchestrator.monitoring': ['semantic_search'],
    },
    'routes.admin_routes': {
        'Orchestrator.config': ['CFG', 'VOL_PATH', 'SNAPSHOT_INDEX', 'MANIFEST', 'ARC_DIR', 'ARTIFACTS_DIR', 'UPLOADS_DIR', 'START_RX', 'END_RX', 'USERS_DEFAULT', 'OPENAI_API_KEY', 'GOOGLE_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS', 'GOOGLE_AUTH_AVAILABLE', 'USE_CLOUD_TTS', 'AUDIO_ENGINE', 'STT_MODEL', 'TTS_MODEL', 'CTX_MAX', 'CURRENT_OPERATOR', 'CHECKPOINT_AUTO_CREATE_INTERVAL', 'CHECKPOINT_MIN_SNAPSHOTS', 'CHECKPOINT_TURNS_TO_COMPRESS'],
        'Orchestrator.volume': ['verify_gm_or_halt', 'read_text_safe', 'parse_tail', 'sha256_bytes', 'cleanup_old_archives'],
        'Orchestrator.fossils': ['hybrid_retrieve', 'get_recent_fossils_for_operator', 'rebuild_snapshot_index', 'extract_snap_ids'],
        'Orchestrator.monitoring': ['get_disk_usage', 'drift_state_global', 'cleanup_stuck_tasks'],
        'Orchestrator.state': ['get_state'],
        'Orchestrator.checkpoint': ['perform_mint', 'create_checkpoint_async'],
        'Orchestrator.models': ['worker_running'],
    },
    'routes.tts_routes': {
        'Orchestrator.config': ['GOOGLE_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS', 'GOOGLE_AUTH_AVAILABLE', 'USE_CLOUD_TTS', 'GOOGLE_TTS_SYNTHESIZE_URL', 'GOOGLE_TTS_VOICES_URL', 'OPENAI_TTS_URL', 'OPENAI_STT_URL', 'TTS_MODEL', 'TTS_VOICE', 'TTS_FORMAT', 'TTS_TIMEOUT', 'STT_MODEL', 'AUDIO_ENGINE', 'LYRIA_MUSIC_URL', 'GEMINI_BASE_URL', 'CLOUD_TTS_URL', 'UPLOADS_DIR', 'VOL_PATH'],
        'Orchestrator.models': ['TaskType'],
        'Orchestrator.state': ['get_operator_preference', 'set_operator_preference', 'save_operator_preferences'],
        'Orchestrator.fossils': ['load_snapshot_index'],
        'Orchestrator.tasks': ['create_task'],
    },
    'routes.chat_routes': {
        'Orchestrator.config': ['OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_URL', 'ANTHROPIC_URL', 'GEMINI_BASE_URL', 'CFG', 'USERS_DEFAULT', 'VOL_PATH', 'UPLOADS_DIR', 'TOKENS_THRESHOLD', 'TURNS_THRESHOLD', 'AUTO_ENABLE', 'DEBOUNCE_MS', 'ON_YELLOW', 'ON_RED'],
        'Orchestrator.models': ['TaskType'],
        'Orchestrator.state': ['get_state', 'save_operator_state'],
        'Orchestrator.volume': ['now_utc_iso', 'read_text_safe'],
        'Orchestrator.fossils': ['get_recent_fossils_for_operator', 'get_recent_checkpoints_for_operator', 'hybrid_retrieve', 'extract_snap_ids'],
        'Orchestrator.monitoring': ['drift_state_for'],
        'Orchestrator.checkpoint': ['perform_mint', 'should_create_checkpoint'],
        'Orchestrator.artifacts': ['parse_and_process_artifacts'],
        'Orchestrator.tasks': ['create_task'],
    },
    'routes.agent_routes': {
        'Orchestrator.config': ['USERS_DEFAULT'],
        'Orchestrator.models': ['AgentSession', 'RegisteredApp', 'AGENT_PERMISSION_PATTERNS', 'AGENT_AUTO_APPROVE_PATTERNS'],
        'Orchestrator.state': ['save_operator_state', 'save_app_registry'],
        'Orchestrator.volume': ['now_utc_iso'],
    },
}


def apply_imports_to_module(module_name: str, imports_dict: Dict[str, List[str]]):
    """Add imports to a module file."""

    # Get module path
    module_path = ORCHESTRATOR_DIR / module_name.replace('.', '/').replace('routes/', 'routes/')
    if not module_path.name.endswith('.py'):
        module_path = Path(str(module_path) + '.py')

    if not module_path.exists():
        print(f"⚠️  {module_name} not found at {module_path}")
        return False

    content = module_path.read_text()
    lines = content.split('\n')

    # Find where to insert imports (after existing imports, before code)
    insert_pos = 0
    in_docstring = False
    last_import_line = 0

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Track docstrings
        if '"""' in line or "'''" in line:
            in_docstring = not in_docstring
            continue

        if in_docstring:
            continue

        # Track last import
        if stripped.startswith('import ') or stripped.startswith('from '):
            last_import_line = i

        # First non-import, non-comment, non-blank line after imports
        if last_import_line > 0 and stripped and not stripped.startswith('#') and not stripped.startswith('from ') and not stripped.startswith('import '):
            insert_pos = last_import_line + 1
            break

    # Build import section
    new_imports = ["\n# Additional imports (auto-generated by dependency audit)"]

    for source_module, names in sorted(imports_dict.items()):
        if names:
            new_imports.append(f"from {source_module} import {', '.join(sorted(names))}")

    new_imports.append("")  # Blank line

    # Insert imports
    lines[insert_pos:insert_pos] = new_imports

    # Write back
    module_path.write_text('\n'.join(lines))

    print(f"✅ Updated {module_name} with {sum(len(v) for v in imports_dict.values())} imports")
    return True


def main():
    print("="*70)
    print("APPLY DEPENDENCY FIXES")
    print("="*70)
    print()

    fixed_count = 0
    for module_name, imports_dict in DEPENDENCY_FIXES.items():
        if imports_dict:
            if apply_imports_to_module(module_name, imports_dict):
                fixed_count += 1

    print()
    print("="*70)
    print(f"✅ Applied dependency fixes to {fixed_count} modules")
    print("="*70)
    print()
    print("Next step: Test app_refactored.py")
    print("  Orchestrator/venv/bin/uvicorn Orchestrator.app_refactored:app --host 0.0.0.0 --port 9091")

    return 0


if __name__ == "__main__":
    exit(main())
