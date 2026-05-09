"""
ToolVault Phase 1 - Core Tests

Tests the complete round-trip:
  mint_tool → volume append → manifest index → byte-offset read → parse → verify

Also tests:
  - Volume rebuild from scratch (manifest recovery)
  - Block formatting and parsing
  - Manifest CRUD operations
  - Multiple tool minting (number auto-increment)
  - Keyword search (embedding-free)
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Patch config paths BEFORE importing toolvault modules
_test_dir = tempfile.mkdtemp(prefix="toolvault_test_")

import Orchestrator.toolvault.config as tv_config
tv_config.TOOLVAULT_DIR = Path(_test_dir)
tv_config.VOLUME_PATH = Path(_test_dir) / "toolvault_volume.txt"
tv_config.MANIFEST_PATH = Path(_test_dir) / "toolvault_manifest.json"

from Orchestrator.toolvault.volume import (
    format_tool_block,
    append_tool_block,
    read_tool_by_offset,
    parse_tool_block,
    rebuild_volume_index,
    read_volume_bytes,
)
from Orchestrator.toolvault.manifest import (
    load_manifest,
    save_manifest,
    register_tool,
    get_tool,
    update_tool_embedding,
    list_tools,
    get_next_number,
    get_manifest_stats,
    invalidate_cache,
    _empty_manifest,
)
from Orchestrator.toolvault.embeddings import (
    cosine_similarity,
    keyword_search,
)
from Orchestrator.toolvault.config import (
    tool_start_anchor,
    tool_end_anchor,
    START_RX,
    END_RX,
    EM_DASH,
)


# Sample tool definitions for testing
def _clean_test_state():
    """Reset all test state: volume, manifest, and cache."""
    if tv_config.VOLUME_PATH.exists():
        tv_config.VOLUME_PATH.unlink()
    if tv_config.MANIFEST_PATH.exists():
        tv_config.MANIFEST_PATH.unlink()
    invalidate_cache()


SAMPLE_WEB_SEARCH = {
    "name": "web_search",
    "description": "Search the web using Perplexity Sonar AI search. Returns synthesized answers with citations.",
    "category": "web",
    "groups": ["chat", "realtime", "mcp"],
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query"
            },
            "search_recency_filter": {
                "type": "string",
                "enum": ["hour", "day", "week", "month"],
                "default": "month",
                "description": "Filter by recency"
            }
        },
        "required": ["query"]
    },
    "returns": "Synthesized answer with source citations",
    "example": 'web_search(query="latest AI news", search_recency_filter="week")',
    "notes": "Falls back to DuckDuckGo if Perplexity key is missing.",
}

SAMPLE_SEND_SMS = {
    "name": "send_sms",
    "description": "Send an SMS text message via the TG200 cellular gateway.",
    "category": "communication",
    "groups": ["chat", "phone", "mcp"],
    "parameters": {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Phone number to send to"
            },
            "message": {
                "type": "string",
                "description": "The message text"
            }
        },
        "required": ["to", "message"]
    },
    "returns": "Confirmation with message ID",
    "example": 'send_sms(to="+15551234567", message="Hello!")',
    "notes": "Auto-splits messages over 160 characters.",
}

SAMPLE_GENERATE_IMAGE = {
    "name": "generate_image",
    "description": "Generate images from text descriptions using Imagen. Supports text-to-image and image-to-image.",
    "category": "media_generation",
    "groups": ["chat", "realtime", "gemini_live", "grok_live", "mcp"],
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Detailed image description"
            },
            "aspectRatio": {
                "type": "string",
                "enum": ["1:1", "16:9", "9:16"],
                "default": "16:9",
                "description": "Aspect ratio"
            }
        },
        "required": ["prompt"]
    },
    "returns": "task_id for async tracking",
    "example": 'generate_image(prompt="A sunset over mountains", aspectRatio="16:9")',
    "notes": "Higher resolution takes longer. 1K default.",
}


class TestAnchors(unittest.TestCase):
    """Test anchor formatting and regex matching."""

    def test_anchor_format(self):
        start = tool_start_anchor(1, "web_search")
        self.assertEqual(start, f"=== START TOOL 001 {EM_DASH} web_search ===")

        end = tool_end_anchor(1, "web_search")
        self.assertEqual(end, f"=== END TOOL 001 {EM_DASH} web_search ===")

    def test_anchor_regex_match(self):
        start = tool_start_anchor(42, "generate_image")
        m = START_RX.search(start)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "042")
        self.assertEqual(m.group(2), "generate_image")

    def test_anchor_number_padding(self):
        """Numbers are zero-padded to 3 digits."""
        self.assertIn("001", tool_start_anchor(1, "test"))
        self.assertIn("042", tool_start_anchor(42, "test"))
        self.assertIn("999", tool_start_anchor(999, "test"))


class TestBlockFormat(unittest.TestCase):
    """Test tool block formatting and parsing."""

    def test_format_block(self):
        block = format_tool_block(number=1, **SAMPLE_WEB_SEARCH)
        self.assertIn(f"=== START TOOL 001 {EM_DASH} web_search ===", block)
        self.assertIn(f"=== END TOOL 001 {EM_DASH} web_search ===", block)
        self.assertIn("NAME: web_search", block)
        self.assertIn("DESCRIPTION: Search the web", block)
        self.assertIn("CATEGORY: web", block)
        self.assertIn("GROUPS: chat, realtime, mcp", block)
        self.assertIn("JSON_SCHEMA:", block)

    def test_parse_block_round_trip(self):
        block = format_tool_block(number=1, **SAMPLE_WEB_SEARCH)
        parsed = parse_tool_block(block)

        self.assertEqual(parsed["NAME"], "web_search")
        self.assertIn("Perplexity", parsed["DESCRIPTION"])
        self.assertEqual(parsed["CATEGORY"], "web")
        self.assertIsInstance(parsed["GROUPS"], list)
        self.assertIn("chat", parsed["GROUPS"])
        self.assertIsInstance(parsed["JSON_SCHEMA"], dict)
        self.assertEqual(parsed["JSON_SCHEMA"]["required"], ["query"])

    def test_json_schema_preserved(self):
        """JSON_SCHEMA must round-trip exactly."""
        block = format_tool_block(number=1, **SAMPLE_WEB_SEARCH)
        parsed = parse_tool_block(block)
        schema = parsed["JSON_SCHEMA"]

        self.assertEqual(schema["type"], "object")
        self.assertIn("query", schema["properties"])
        self.assertEqual(schema["properties"]["query"]["type"], "string")


class TestVolumeIO(unittest.TestCase):
    """Test volume append and byte-offset retrieval."""

    def setUp(self):
        _clean_test_state()

    def test_append_and_read(self):
        block = format_tool_block(number=1, **SAMPLE_WEB_SEARCH)
        start, end = append_tool_block(block)

        self.assertEqual(start, 0)
        self.assertGreater(end, start)

        # Read back by offset
        text = read_tool_by_offset(start, end)
        self.assertIn("web_search", text)
        self.assertIn("START TOOL 001", text)
        self.assertIn("END TOOL 001", text)

    def test_multiple_appends(self):
        """Multiple tools get sequential, non-overlapping byte ranges."""
        b1 = format_tool_block(number=1, **SAMPLE_WEB_SEARCH)
        s1, e1 = append_tool_block(b1)

        b2 = format_tool_block(number=2, **SAMPLE_SEND_SMS)
        s2, e2 = append_tool_block(b2)

        b3 = format_tool_block(number=3, **SAMPLE_GENERATE_IMAGE)
        s3, e3 = append_tool_block(b3)

        # No overlaps
        self.assertGreaterEqual(s2, e1)
        self.assertGreaterEqual(s3, e2)

        # Each reads back correctly
        t1 = read_tool_by_offset(s1, e1)
        t2 = read_tool_by_offset(s2, e2)
        t3 = read_tool_by_offset(s3, e3)

        self.assertIn("web_search", t1)
        self.assertIn("send_sms", t2)
        self.assertIn("generate_image", t3)

    def test_empty_volume_read(self):
        text = read_tool_by_offset(0, 100)
        self.assertEqual(text, "")


class TestVolumeRebuild(unittest.TestCase):
    """Test index rebuild from volume (recovery path)."""

    def setUp(self):
        _clean_test_state()

    def test_rebuild_finds_all_tools(self):
        # Mint 3 tools directly to volume
        b1 = format_tool_block(number=1, **SAMPLE_WEB_SEARCH)
        append_tool_block(b1)

        b2 = format_tool_block(number=2, **SAMPLE_SEND_SMS)
        append_tool_block(b2)

        b3 = format_tool_block(number=3, **SAMPLE_GENERATE_IMAGE)
        append_tool_block(b3)

        # Rebuild index from volume
        tools = rebuild_volume_index()

        self.assertEqual(len(tools), 3)
        self.assertIn("web_search", tools)
        self.assertIn("send_sms", tools)
        self.assertIn("generate_image", tools)

        # Verify byte offsets work
        for name, data in tools.items():
            text = read_tool_by_offset(data["byte_start"], data["byte_end"])
            self.assertIn(name, text)

    def test_rebuild_empty_volume(self):
        tools = rebuild_volume_index()
        self.assertEqual(tools, {})


class TestManifest(unittest.TestCase):
    """Test manifest CRUD operations."""

    def setUp(self):
        _clean_test_state()

    def test_empty_manifest(self):
        m = load_manifest()
        self.assertEqual(m["meta"]["total_tools"], 0)
        self.assertEqual(len(m["tools"]), 0)

    def test_register_and_get(self):
        entry = register_tool(
            name="web_search",
            number=1,
            byte_start=0,
            byte_end=500,
            category="web",
            groups=["chat", "mcp"],
        )

        got = get_tool("web_search")
        self.assertIsNotNone(got)
        self.assertEqual(got["number"], 1)
        self.assertEqual(got["byte_start"], 0)
        self.assertEqual(got["byte_end"], 500)
        self.assertEqual(got["category"], "web")

    def test_auto_increment_number(self):
        register_tool("a", 1, 0, 100, "cat", [])
        self.assertEqual(get_next_number(), 2)

        register_tool("b", 2, 100, 200, "cat", [])
        self.assertEqual(get_next_number(), 3)

        register_tool("c", 5, 200, 300, "cat", [])
        self.assertEqual(get_next_number(), 6)

    def test_list_tools_with_filter(self):
        register_tool("web_search", 1, 0, 100, "web", ["chat"])
        register_tool("send_sms", 2, 100, 200, "communication", ["chat"])
        register_tool("generate_image", 3, 200, 300, "media", ["chat"])

        all_tools = list_tools()
        self.assertEqual(len(all_tools), 3)

        web_tools = list_tools(category="web")
        self.assertEqual(len(web_tools), 1)
        self.assertEqual(web_tools[0]["name"], "web_search")

    def test_update_embedding(self):
        register_tool("test", 1, 0, 100, "test", [])

        fake_embedding = [0.1] * 10
        result = update_tool_embedding("test", fake_embedding)
        self.assertTrue(result)

        got = get_tool("test")
        self.assertEqual(got["embedding"], fake_embedding)

    def test_update_nonexistent_returns_false(self):
        result = update_tool_embedding("nonexistent", [0.1])
        self.assertFalse(result)

    def test_manifest_stats(self):
        register_tool("a", 1, 0, 100, "web", [], tier=1)
        register_tool("b", 2, 100, 200, "web", [], tier=2)
        register_tool("c", 3, 200, 300, "media", [], tier=2)

        stats = get_manifest_stats()
        self.assertEqual(stats["total_tools"], 3)
        self.assertEqual(stats["by_tier"][1], 1)
        self.assertEqual(stats["by_tier"][2], 2)
        self.assertEqual(stats["by_category"]["web"], 2)
        self.assertEqual(stats["by_category"]["media"], 1)

    def test_cache_invalidation(self):
        """Cache should return fresh data after invalidation."""
        register_tool("test", 1, 0, 100, "cat", [])
        m1 = load_manifest()
        self.assertEqual(len(m1["tools"]), 1)

        invalidate_cache()
        m2 = load_manifest()
        self.assertEqual(len(m2["tools"]), 1)


class TestCosineSimilarity(unittest.TestCase):
    """Test vector similarity calculation."""

    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(cosine_similarity(v, v), 1.0, places=5)

    def test_orthogonal_vectors(self):
        v1 = [1.0, 0.0]
        v2 = [0.0, 1.0]
        self.assertAlmostEqual(cosine_similarity(v1, v2), 0.0, places=5)

    def test_opposite_vectors(self):
        v1 = [1.0, 0.0]
        v2 = [-1.0, 0.0]
        self.assertAlmostEqual(cosine_similarity(v1, v2), -1.0, places=5)

    def test_empty_vectors(self):
        self.assertEqual(cosine_similarity([], []), 0.0)

    def test_mismatched_lengths(self):
        self.assertEqual(cosine_similarity([1.0], [1.0, 2.0]), 0.0)


class TestKeywordSearch(unittest.TestCase):
    """Test keyword-based tool search (no embeddings needed)."""

    def test_exact_name_match(self):
        tools = {"web_search": {"category": "web"}}
        descs = {"web_search": "Search the web"}

        results = keyword_search("web_search", tools, descs)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "web_search")

    def test_description_match(self):
        tools = {
            "send_sms": {"category": "communication"},
            "web_search": {"category": "web"},
        }
        descs = {
            "send_sms": "Send an SMS text message via cellular gateway",
            "web_search": "Search the web for information",
        }

        results = keyword_search("text message", tools, descs)
        self.assertGreater(len(results), 0)
        # send_sms should rank higher
        self.assertEqual(results[0][0], "send_sms")

    def test_category_match(self):
        tools = {
            "send_sms": {"category": "communication"},
            "make_phone_call": {"category": "communication"},
            "web_search": {"category": "web"},
        }
        descs = {
            "send_sms": "Send SMS",
            "make_phone_call": "Make a phone call",
            "web_search": "Search web",
        }

        results = keyword_search("communication", tools, descs)
        names = [r[0] for r in results]
        self.assertIn("send_sms", names)
        self.assertIn("make_phone_call", names)


class TestFullRoundTrip(unittest.TestCase):
    """Integration test: mint → index → read → parse → verify."""

    def setUp(self):
        _clean_test_state()

    @patch("Orchestrator.toolvault.embeddings.generate_embedding")
    def test_mint_tool_full_pipeline(self, mock_embed):
        """Test mint_tool() with mocked embeddings."""
        from Orchestrator.toolvault import mint_tool

        fake_embedding = [0.5] * 3072
        mock_embed.return_value = fake_embedding

        result = mint_tool(**SAMPLE_WEB_SEARCH)

        # Verify result
        self.assertEqual(result["name"], "web_search")
        self.assertEqual(result["number"], 1)
        self.assertGreater(result["byte_end"], result["byte_start"])
        self.assertEqual(result["category"], "web")

        # Verify manifest
        got = get_tool("web_search")
        self.assertIsNotNone(got)
        self.assertEqual(got["embedding"], fake_embedding)

        # Verify round-trip read
        from Orchestrator.toolvault import read_tool
        parsed = read_tool("web_search")
        self.assertEqual(parsed["NAME"], "web_search")
        self.assertIn("Perplexity", parsed["DESCRIPTION"])
        self.assertIsInstance(parsed["JSON_SCHEMA"], dict)

    @patch("Orchestrator.toolvault.embeddings.generate_embedding")
    def test_mint_multiple_tools(self, mock_embed):
        """Multiple tools get sequential numbers and non-overlapping offsets."""
        from Orchestrator.toolvault import mint_tool, read_tool

        mock_embed.return_value = [0.1] * 3072

        r1 = mint_tool(**SAMPLE_WEB_SEARCH)
        r2 = mint_tool(**SAMPLE_SEND_SMS)
        r3 = mint_tool(**SAMPLE_GENERATE_IMAGE)

        self.assertEqual(r1["number"], 1)
        self.assertEqual(r2["number"], 2)
        self.assertEqual(r3["number"], 3)

        # No byte-range overlaps
        self.assertGreaterEqual(r2["byte_start"], r1["byte_end"])
        self.assertGreaterEqual(r3["byte_start"], r2["byte_end"])

        # Each reads back correctly
        p1 = read_tool("web_search")
        p2 = read_tool("send_sms")
        p3 = read_tool("generate_image")

        self.assertEqual(p1["NAME"], "web_search")
        self.assertEqual(p2["NAME"], "send_sms")
        self.assertEqual(p3["NAME"], "generate_image")

    @patch("Orchestrator.toolvault.embeddings.generate_embedding")
    def test_rebuild_recovers_all(self, mock_embed):
        """Rebuild from volume recovers all tools after manifest loss."""
        from Orchestrator.toolvault import mint_tool, rebuild_index

        mock_embed.return_value = [0.1] * 3072

        mint_tool(**SAMPLE_WEB_SEARCH)
        mint_tool(**SAMPLE_SEND_SMS)

        # Delete manifest (simulate corruption)
        tv_config.MANIFEST_PATH.unlink(missing_ok=True)
        invalidate_cache()

        # Rebuild
        recovered = rebuild_index()
        self.assertEqual(len(recovered["tools"]), 2)
        self.assertIn("web_search", recovered["tools"])
        self.assertIn("send_sms", recovered["tools"])


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def tearDownModule():
    """Remove test directory."""
    if os.path.exists(_test_dir):
        shutil.rmtree(_test_dir)


if __name__ == "__main__":
    unittest.main()
