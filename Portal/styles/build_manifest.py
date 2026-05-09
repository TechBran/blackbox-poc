#!/usr/bin/env python3
"""
build_manifest.py - CSS-aware modularization with proper block parsing.

This script parses CSS into complete rulesets, preserving block integrity,
then groups them by selector patterns into modular files.

Usage:
    python3 build_manifest.py [--extract]

Options:
    --extract   Extract CSS into module files
"""
import re
import json
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
import sys

# Paths
PORTAL_DIR = Path(__file__).parent.parent
CSS_SOURCE = PORTAL_DIR / "style.css"
STYLES_DIR = Path(__file__).parent
MANIFEST_PATH = STYLES_DIR / "css_manifest.json"

@dataclass
class CSSBlock:
    """A complete CSS block (rule, @-rule, or comment)."""
    block_type: str  # 'rule', 'atrule', 'comment', 'keyframes'
    selector: str    # Selector or @-rule name
    content: str     # Full content including braces
    line_start: int
    line_end: int
    byte_start: int
    byte_end: int
    target_module: str = "_utilities.css"

# Module assignment patterns: (selector_pattern, module_path)
MODULE_PATTERNS = [
    # Foundation - Variables
    (r'^:root$', '_variables.css'),
    (r'^\*$', '_base.css'),

    # Foundation - Base/Reset
    (r'^html', '_base.css'),
    (r'^body', '_base.css'),
    (r'^\.app$', '_base.css'),

    # Components - Topbar
    (r'^\.topbar', 'components/_topbar.css'),
    (r'^\.brand', 'components/_topbar.css'),
    (r'^\.actions', 'components/_topbar.css'),
    (r'^\.floating-bubble', 'components/_topbar.css'),
    (r'^\.operator-', 'components/_topbar.css'),
    (r'^#operatorSelect', 'components/_topbar.css'),
    (r'^\.mode-toggle', 'components/_topbar.css'),

    # Components - Buttons
    (r'^\.btn', 'components/_buttons.css'),
    (r'^button', 'components/_buttons.css'),
    (r'\.btn-', 'components/_buttons.css'),
    (r'^\.toolbar-btn', 'components/_buttons.css'),
    (r'^\.polished-btn', 'components/_buttons.css'),
    (r'^\.action-btn', 'components/_buttons.css'),
    (r'^\.mic-btn', 'components/_buttons.css'),
    (r'^\.send-btn', 'components/_buttons.css'),
    (r'^\.quick-action', 'components/_buttons.css'),

    # Components - Controls/Dropdowns
    (r'^select', 'components/_controls.css'),
    (r'^\.dropdown', 'components/_controls.css'),
    (r'^\.control-row', 'components/_controls.css'),

    # Components - Composer
    (r'^\.composer', 'components/_composer.css'),
    (r'^\.textarea', 'components/_composer.css'),
    (r'^#userInput', 'components/_composer.css'),
    (r'^\.input-row', 'components/_composer.css'),
    (r'^\.attach-', 'components/_composer.css'),
    (r'^\.paperclip', 'components/_composer.css'),

    # Components - Modals
    (r'^\.modal', 'components/_modals.css'),
    (r'^\.modal-', 'components/_modals.css'),
    (r'^#menuModal', 'components/_modals.css'),
    (r'^#confirmModal', 'components/_modals.css'),
    (r'^#addOperatorModal', 'components/_modals.css'),
    (r'^\.menu-', 'components/_modals.css'),

    # Components - Chat Bubbles
    (r'^\.bubble', 'components/_chat-bubbles.css'),
    (r'^\.chat-area', 'components/_chat-bubbles.css'),
    (r'^\.bubbles', 'components/_chat-bubbles.css'),
    (r'^#bubbles', 'components/_chat-bubbles.css'),
    (r'^\.user-bubble', 'components/_chat-bubbles.css'),
    (r'^\.assistant-bubble', 'components/_chat-bubbles.css'),
    (r'^\.copybtn', 'components/_chat-bubbles.css'),
    (r'^\.playbtn', 'components/_chat-bubbles.css'),

    # Components - Response Panel
    (r'^\.response-panel', 'components/_response.css'),
    (r'^\.bubble-controls', 'components/_response.css'),

    # Components - Audio Player
    (r'^\.audio-', 'components/_audio-player.css'),
    (r'^\.custom-audio', 'components/_audio-player.css'),

    # Features - Markdown
    (r'^\.markdown', 'features/_markdown.css'),
    (r'^pre', 'features/_markdown.css'),
    (r'^code', 'features/_markdown.css'),
    (r'^\.code-block', 'features/_markdown.css'),
    (r'^\.hljs', 'features/_markdown.css'),
    (r'^\.diff-', 'features/_markdown.css'),
    (r'^blockquote', 'features/_markdown.css'),

    # Features - Timeline
    (r'^\.timeline', 'features/_timeline.css'),
    (r'^#timeline', 'features/_timeline.css'),
    (r'^\.snapshot-', 'features/_timeline.css'),

    # Features - Task Monitor
    (r'^\.task-', 'features/_task-monitor.css'),
    (r'^#taskMonitor', 'features/_task-monitor.css'),
    (r'^\.bg-task', 'features/_task-monitor.css'),

    # Features - Thinking Panel
    (r'^\.thinking', 'features/_thinking.css'),
    (r'^#thinking', 'features/_thinking.css'),
    (r'^\.neural', 'features/_thinking.css'),
    (r'^\.thought-', 'features/_thinking.css'),

    # Features - Settings
    (r'^\.settings', 'features/_settings.css'),
    (r'^\.pref-', 'features/_settings.css'),
    (r'^\.voice-pref', 'features/_settings.css'),
    (r'^\.system-controls', 'features/_settings.css'),
    (r'^\.advanced-settings', 'features/_settings.css'),
    (r'^\.running-apps', 'features/_settings.css'),
    (r'^\.generation-section', 'features/_settings.css'),

    # Features - File Upload
    (r'^\.upload', 'features/_file-upload.css'),
    (r'^\.preview', 'features/_file-upload.css'),
    (r'^\.file-', 'features/_file-upload.css'),
    (r'^\.media-preview', 'features/_file-upload.css'),

    # Generation - Base
    (r'^\.gen-modal', 'generation/_base.css'),
    (r'^\.generation-modal', 'generation/_base.css'),
    (r'^\.generation-card', 'generation/_base.css'),
    (r'^\.generation-prompt', 'generation/_base.css'),
    (r'^\.whisper-', 'generation/_base.css'),

    # Generation - Image
    (r'^\.image-gen', 'generation/_image.css'),
    (r'^#imageGen', 'generation/_image.css'),
    (r'^\.reference-', 'generation/_image.css'),
    (r'^\.nano-banana', 'generation/_image.css'),
    (r'^\.imagen', 'generation/_image.css'),

    # Generation - Video
    (r'^\.video-gen', 'generation/_video.css'),
    (r'^#videoGen', 'generation/_video.css'),
    (r'^\.veo-', 'generation/_video.css'),
    (r'^\.video-prompt', 'generation/_video.css'),
    (r'^\.video-settings', 'generation/_video.css'),

    # Generation - Audio/Music
    (r'^\.music-', 'generation/_audio.css'),
    (r'^#musicGen', 'generation/_audio.css'),
    (r'^\.lyria', 'generation/_audio.css'),
    (r'^\.tts-', 'generation/_audio.css'),
    (r'^\.ssml', 'generation/_audio.css'),
    (r'^\.gemini-tts', 'generation/_audio.css'),
    (r'^\.google-tts', 'generation/_audio.css'),

    # Agents - Base
    (r'^\.agent-', 'agents/_base.css'),
    (r'^\.permission-', 'agents/_base.css'),
    (r'^\.app-registry', 'agents/_base.css'),
    (r'^\.terminal', 'agents/_base.css'),

    # Agents - Claude
    (r'^\.claude', 'agents/_claude.css'),
    (r'^#claude', 'agents/_claude.css'),

    # Agents - Gemini
    (r'^\.gemini(?!-live)', 'agents/_gemini.css'),
    (r'^#gemini(?!Live)', 'agents/_gemini.css'),

    # Agents - Realtime
    (r'^\.realtime', 'agents/_realtime.css'),
    (r'^#realtime', 'agents/_realtime.css'),
    (r'^\.gpt-realtime', 'agents/_realtime.css'),
    (r'^\.gemini-live', 'agents/_realtime.css'),
    (r'^#geminiLive', 'agents/_realtime.css'),
    (r'^\.transcript', 'agents/_realtime.css'),
    (r'^\.voice-', 'agents/_realtime.css'),

    # Utilities - Animations
    (r'^@keyframes', '_utilities.css'),
    (r'^\.hide', '_utilities.css'),
    (r'^\.show', '_utilities.css'),
    (r'^\.active', '_utilities.css'),
    (r'^\.collapsed', '_utilities.css'),
    (r'^\.expanded', '_utilities.css'),
    (r'^\.loading', '_utilities.css'),
    (r'^\.error', '_utilities.css'),
    (r'^\.success', '_utilities.css'),

    # Utilities - Media Queries (will be handled specially)
    (r'^@media', '_utilities.css'),
]


def parse_css_blocks(css_text: str) -> List[CSSBlock]:
    """
    Parse CSS into complete blocks, respecting brace nesting.
    Returns list of CSSBlock objects.
    """
    blocks = []
    css_bytes = css_text.encode('utf-8')

    i = 0
    line_num = 1

    while i < len(css_text):
        # Skip whitespace
        while i < len(css_text) and css_text[i] in ' \t\n\r':
            if css_text[i] == '\n':
                line_num += 1
            i += 1

        if i >= len(css_text):
            break

        start_pos = i
        start_line = line_num
        byte_start = len(css_text[:i].encode('utf-8'))

        # Check for comment
        if css_text[i:i+2] == '/*':
            # Find end of comment
            end = css_text.find('*/', i + 2)
            if end == -1:
                end = len(css_text)
            else:
                end += 2

            content = css_text[i:end]
            line_num += content.count('\n')
            byte_end = len(css_text[:end].encode('utf-8'))

            blocks.append(CSSBlock(
                block_type='comment',
                selector='',
                content=content,
                line_start=start_line,
                line_end=line_num,
                byte_start=byte_start,
                byte_end=byte_end
            ))
            i = end
            continue

        # Check for @-rule
        if css_text[i] == '@':
            # Find the @-rule name
            j = i + 1
            while j < len(css_text) and css_text[j] not in ' \t\n\r{;':
                j += 1
            at_rule = css_text[i:j]

            # Handle @keyframes (has block)
            if at_rule == '@keyframes':
                # Find opening brace
                brace_pos = css_text.find('{', j)
                if brace_pos == -1:
                    i = j
                    continue

                # Get keyframe name
                selector = css_text[i:brace_pos].strip()

                # Find matching closing brace (handle nested)
                depth = 1
                k = brace_pos + 1
                while k < len(css_text) and depth > 0:
                    if css_text[k] == '{':
                        depth += 1
                    elif css_text[k] == '}':
                        depth -= 1
                    k += 1

                content = css_text[i:k]
                line_num += content.count('\n')
                byte_end = len(css_text[:k].encode('utf-8'))

                blocks.append(CSSBlock(
                    block_type='keyframes',
                    selector=selector,
                    content=content,
                    line_start=start_line,
                    line_end=line_num,
                    byte_start=byte_start,
                    byte_end=byte_end,
                    target_module='_utilities.css'
                ))
                i = k
                continue

            # Handle @media (has block)
            elif at_rule == '@media':
                # Find opening brace
                brace_pos = css_text.find('{', j)
                if brace_pos == -1:
                    i = j
                    continue

                # Find matching closing brace (handle nested)
                depth = 1
                k = brace_pos + 1
                while k < len(css_text) and depth > 0:
                    if css_text[k] == '{':
                        depth += 1
                    elif css_text[k] == '}':
                        depth -= 1
                    k += 1

                content = css_text[i:k]
                selector = css_text[i:brace_pos].strip()
                line_num += content.count('\n')
                byte_end = len(css_text[:k].encode('utf-8'))

                # Analyze content to determine best module
                target = determine_media_query_module(content)

                blocks.append(CSSBlock(
                    block_type='media',
                    selector=selector,
                    content=content,
                    line_start=start_line,
                    line_end=line_num,
                    byte_start=byte_start,
                    byte_end=byte_end,
                    target_module=target
                ))
                i = k
                continue

            # Other @-rules (like @import, @charset) - find semicolon or block
            else:
                # Check for block or semicolon
                semi = css_text.find(';', j)
                brace = css_text.find('{', j)

                if semi != -1 and (brace == -1 or semi < brace):
                    # Ends with semicolon
                    content = css_text[i:semi+1]
                    line_num += content.count('\n')
                    byte_end = len(css_text[:semi+1].encode('utf-8'))

                    blocks.append(CSSBlock(
                        block_type='atrule',
                        selector=at_rule,
                        content=content,
                        line_start=start_line,
                        line_end=line_num,
                        byte_start=byte_start,
                        byte_end=byte_end
                    ))
                    i = semi + 1
                    continue
                elif brace != -1:
                    # Has block - find matching brace
                    depth = 1
                    k = brace + 1
                    while k < len(css_text) and depth > 0:
                        if css_text[k] == '{':
                            depth += 1
                        elif css_text[k] == '}':
                            depth -= 1
                        k += 1

                    content = css_text[i:k]
                    line_num += content.count('\n')
                    byte_end = len(css_text[:k].encode('utf-8'))

                    blocks.append(CSSBlock(
                        block_type='atrule',
                        selector=at_rule,
                        content=content,
                        line_start=start_line,
                        line_end=line_num,
                        byte_start=byte_start,
                        byte_end=byte_end
                    ))
                    i = k
                    continue

        # Regular rule - find selector then block
        # Selector ends at opening brace
        brace_pos = css_text.find('{', i)
        if brace_pos == -1:
            # No more rules
            break

        selector = css_text[i:brace_pos].strip()

        # Skip if selector is empty (leftover from parsing)
        if not selector:
            i = brace_pos + 1
            continue

        # Find matching closing brace
        depth = 1
        k = brace_pos + 1
        while k < len(css_text) and depth > 0:
            if css_text[k] == '{':
                depth += 1
            elif css_text[k] == '}':
                depth -= 1
            k += 1

        content = css_text[i:k]
        line_num += content.count('\n')
        byte_end = len(css_text[:k].encode('utf-8'))

        # Determine target module from selector
        target = determine_module(selector)

        blocks.append(CSSBlock(
            block_type='rule',
            selector=selector,
            content=content,
            line_start=start_line,
            line_end=line_num,
            byte_start=byte_start,
            byte_end=byte_end,
            target_module=target
        ))
        i = k

    return blocks


def determine_module(selector: str) -> str:
    """Determine which module a selector belongs to."""
    # Normalize selector (first selector in group)
    first_selector = selector.split(',')[0].strip()

    for pattern, module in MODULE_PATTERNS:
        if re.search(pattern, first_selector, re.IGNORECASE):
            return module

    return '_utilities.css'


def determine_media_query_module(content: str) -> str:
    """Determine module for @media query based on its contents."""
    # Count selector patterns in the media query content
    module_counts = {}

    for pattern, module in MODULE_PATTERNS:
        if module == '_utilities.css':
            continue
        count = len(re.findall(pattern, content, re.IGNORECASE))
        if count > 0:
            module_counts[module] = module_counts.get(module, 0) + count

    if module_counts:
        # Return module with most matches
        return max(module_counts, key=module_counts.get)

    return '_utilities.css'


def associate_comments(blocks: List[CSSBlock]) -> List[CSSBlock]:
    """Associate standalone comments with their following rules."""
    result = []
    pending_comments = []

    for block in blocks:
        if block.block_type == 'comment':
            # Check if it's a section comment (we'll keep these)
            if '===' in block.content or '---' in block.content:
                pending_comments.append(block)
            else:
                # Inline comment - attach to next rule
                pending_comments.append(block)
        else:
            # Non-comment block
            if pending_comments:
                # Combine comments with this block
                combined_content = '\n'.join(c.content for c in pending_comments) + '\n' + block.content
                block.content = combined_content
                block.line_start = pending_comments[0].line_start
                block.byte_start = pending_comments[0].byte_start
                pending_comments = []
            result.append(block)

    # Don't lose trailing comments
    result.extend(pending_comments)

    return result


def group_by_module(blocks: List[CSSBlock]) -> Dict[str, List[CSSBlock]]:
    """Group blocks by their target module."""
    modules = {}

    for block in blocks:
        module = block.target_module
        if module not in modules:
            modules[module] = []
        modules[module].append(block)

    return modules


def write_module_file(module_path: Path, blocks: List[CSSBlock], source_file: str) -> int:
    """Write a module file from blocks. Returns bytes written."""
    module_path.parent.mkdir(parents=True, exist_ok=True)

    # Build content
    header = f"""/* {module_path.name}
 * Extracted from: {source_file}
 * Generated: {datetime.now().isoformat()}
 * Blocks: {len(blocks)}
 */

"""

    content = header + '\n\n'.join(block.content for block in blocks)

    with open(module_path, 'w') as f:
        f.write(content)

    return len(content.encode('utf-8'))


def build_manifest(blocks: List[CSSBlock], modules: Dict[str, List[CSSBlock]]) -> Dict:
    """Build manifest from parsed blocks."""
    css_bytes = CSS_SOURCE.read_bytes()

    # Build section info from blocks
    sections = []
    for block in blocks:
        sections.append({
            'type': block.block_type,
            'selector': block.selector[:100] if block.selector else '',
            'line_start': block.line_start,
            'line_end': block.line_end,
            'byte_start': block.byte_start,
            'byte_end': block.byte_end,
            'target_module': block.target_module,
            'size_bytes': block.byte_end - block.byte_start
        })

    # Build module stats
    module_stats = {}
    for module, blocks_list in modules.items():
        module_stats[module] = {
            'blocks': len(blocks_list),
            'bytes': sum(b.byte_end - b.byte_start for b in blocks_list),
            'lines': sum(b.line_end - b.line_start + 1 for b in blocks_list)
        }

    return {
        'version': '2.0.0',
        'parser': 'css-aware',
        'source': {
            'file': str(CSS_SOURCE),
            'size_bytes': len(css_bytes),
            'lines': css_bytes.decode('utf-8').count('\n') + 1,
            'sha256': hashlib.sha256(css_bytes).hexdigest(),
            'preserved': True
        },
        'created_at': datetime.now().isoformat(),
        'modules': module_stats,
        'blocks': sections,
        'js_mapping': {
            "ui-setup.js": ["components/_topbar.css", "_utilities.css"],
            "chat-send.js": ["components/_composer.css", "components/_buttons.css"],
            "chat-bubbles.js": ["components/_chat-bubbles.css", "features/_thinking.css"],
            "generation-modals.js": ["generation/_base.css", "generation/_image.css", "generation/_video.css", "generation/_audio.css"],
            "agent-handler.js": ["agents/_claude.css", "agents/_base.css"],
            "gemini-agent-handler.js": ["agents/_gemini.css", "agents/_base.css"],
            "gpt-realtime.js": ["agents/_realtime.css"],
            "gemini-live.js": ["agents/_realtime.css"],
            "markdown-renderer.js": ["features/_markdown.css"],
            "timeline-browser.js": ["features/_timeline.css"],
            "task-manager.js": ["features/_task-monitor.css"]
        }
    }


def main():
    extract_mode = '--extract' in sys.argv

    print("=" * 60)
    print("CSS-Aware Modularization")
    print("=" * 60)

    # Read source
    print(f"\nReading: {CSS_SOURCE}")
    css_text = CSS_SOURCE.read_text()
    css_bytes = CSS_SOURCE.read_bytes()

    print(f"Size: {len(css_bytes):,} bytes, {css_text.count(chr(10)) + 1:,} lines")
    print(f"SHA-256: {hashlib.sha256(css_bytes).hexdigest()[:16]}...")

    # Parse into blocks
    print("\nParsing CSS blocks...")
    blocks = parse_css_blocks(css_text)
    print(f"Found {len(blocks)} blocks")

    # Associate comments
    print("Associating comments...")
    blocks = associate_comments(blocks)
    print(f"After association: {len(blocks)} blocks")

    # Group by module
    print("Grouping by module...")
    modules = group_by_module(blocks)
    print(f"Target modules: {len(modules)}")

    # Build and save manifest
    manifest = build_manifest(blocks, modules)
    with open(MANIFEST_PATH, 'w') as f:
        json.dump(manifest, f, indent=2)
    print(f"\nManifest saved: {MANIFEST_PATH}")

    # Print summary
    print("\n" + "=" * 60)
    print("MODULE SUMMARY")
    print("=" * 60)

    for module, stats in sorted(manifest['modules'].items()):
        print(f"  {module}: {stats['blocks']} blocks, {stats['bytes']:,} bytes")

    if extract_mode:
        print("\n" + "=" * 60)
        print("EXTRACTING MODULES")
        print("=" * 60)

        # Clean existing files first
        for subdir in ['components', 'features', 'generation', 'agents']:
            dir_path = STYLES_DIR / subdir
            if dir_path.exists():
                for f in dir_path.glob('*.css'):
                    f.unlink()
        for f in STYLES_DIR.glob('_*.css'):
            f.unlink()

        # Write module files
        total_bytes = 0
        for module, blocks_list in sorted(modules.items()):
            module_path = STYLES_DIR / module
            bytes_written = write_module_file(module_path, blocks_list, CSS_SOURCE.name)
            total_bytes += bytes_written
            print(f"  {module}: {bytes_written:,} bytes")

        print(f"\nTotal extracted: {total_bytes:,} bytes")

        # Create main.css
        print("\nCreating main.css...")
        create_main_css(modules)
        print("Done!")

    return manifest


def create_main_css(modules: Dict[str, List[CSSBlock]]) -> None:
    """Create main.css with @imports in dependency order."""

    # Define import order by layer
    layer_order = [
        # Layer 0: Foundation
        '_variables.css',
        '_base.css',
        '_utilities.css',
        # Layer 1: Components
        'components/_buttons.css',
        'components/_controls.css',
        'components/_modals.css',
        'components/_topbar.css',
        'components/_composer.css',
        'components/_chat-bubbles.css',
        'components/_response.css',
        'components/_audio-player.css',
        # Layer 2: Features
        'features/_markdown.css',
        'features/_file-upload.css',
        'features/_task-monitor.css',
        'features/_thinking.css',
        'features/_settings.css',
        # Layer 2: Generation
        'generation/_base.css',
        'generation/_image.css',
        'generation/_video.css',
        'generation/_audio.css',
        # Layer 3: Agents
        'agents/_base.css',
        'agents/_claude.css',
        'agents/_gemini.css',
        'agents/_realtime.css',
    ]

    content = f"""/* Portal/styles/main.css - CSS Module Entry Point
 * Generated: {datetime.now().isoformat()}
 * Source: Portal/style.css (preserved)
 * Rebuild: python3 build_manifest.py --extract
 */

"""

    # Add imports for modules that exist
    current_layer = None
    layer_names = {
        '_': 'FOUNDATION',
        'components/': 'COMPONENTS',
        'features/': 'FEATURES',
        'generation/': 'GENERATION',
        'agents/': 'AGENTS'
    }

    for module in layer_order:
        if module in modules:
            # Check if we need a layer header
            for prefix, layer_name in layer_names.items():
                if module.startswith(prefix) and current_layer != layer_name:
                    current_layer = layer_name
                    content += f"\n/* === {layer_name} === */\n"
                    break

            content += f"@import url('./{module}');\n"

    # Add any modules not in the predefined order
    for module in sorted(modules.keys()):
        if module not in layer_order:
            content += f"@import url('./{module}');\n"

    main_path = STYLES_DIR / 'main.css'
    main_path.write_text(content)
    print(f"  main.css: {len(content)} bytes")


if __name__ == '__main__':
    main()
