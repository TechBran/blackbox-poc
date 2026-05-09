"""Unit tests for ER observation bundle composition.

Verifies that gather_observation() emits the new local-costmap + slam-map
channels and no longer emits the global-costmap channel. Uses light mocking
so no live RosBridge is needed.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from google.genai import types as genai_types

from ugv_tools_api.er import sensors


def _fake_image_part() -> genai_types.Part:
    # Tiny valid PNG payload would be heavier than needed; the consumer of
    # gather_observation just relays Parts, so any inline_data Part works.
    return genai_types.Part.from_bytes(data=b"\x00", mime_type="image/png")


def _fake_state_part() -> genai_types.Part:
    return genai_types.Part.from_text(text="ROBOT_STATE_JSON\n{}")


def _collect_text_labels(parts: list[genai_types.Part]) -> list[str]:
    return [getattr(p, "text", "") or "" for p in parts]


def test_gather_observation_includes_local_costmap_and_slam_map():
    """When all renderers populate, the bundle labels mention all six channels."""
    async def _run():
        with patch.object(sensors, "_render_rgb", new=AsyncMock(return_value=_fake_image_part())), \
             patch.object(sensors, "_render_depth", new=AsyncMock(return_value=_fake_image_part())), \
             patch.object(sensors, "_render_lidar", new=AsyncMock(return_value=_fake_image_part())), \
             patch.object(sensors, "_render_local_costmap", new=AsyncMock(return_value=_fake_image_part())), \
             patch.object(sensors, "_render_slam_map", new=AsyncMock(return_value=_fake_image_part())), \
             patch.object(sensors, "_render_state", new=AsyncMock(return_value=_fake_state_part())):
            return await sensors.gather_observation()

    parts = asyncio.run(_run())
    labels = " ".join(_collect_text_labels(parts))

    assert "OAK-D fixed body camera" in labels, "RGB label should describe OAK-D body cam"
    assert "Depth (OAK-D" in labels, "expected 'Depth (OAK-D' label in bundle"
    assert "Local costmap" in labels, "expected 'Local costmap' label in bundle"
    assert "SLAM map" in labels, "expected 'SLAM map' label in bundle"
    assert "Global costmap" not in labels, "global costmap should be removed from bundle"
    assert "pantilt camera" not in labels, "RGB should source from OAK-D, not pantilt"


def test_gather_observation_omits_local_when_renderer_returns_none():
    """If local-costmap renderer returns None, no local-costmap label is emitted."""
    async def _run():
        with patch.object(sensors, "_render_rgb", new=AsyncMock(return_value=None)), \
             patch.object(sensors, "_render_depth", new=AsyncMock(return_value=None)), \
             patch.object(sensors, "_render_lidar", new=AsyncMock(return_value=None)), \
             patch.object(sensors, "_render_local_costmap", new=AsyncMock(return_value=None)), \
             patch.object(sensors, "_render_slam_map", new=AsyncMock(return_value=_fake_image_part())), \
             patch.object(sensors, "_render_state", new=AsyncMock(return_value=_fake_state_part())):
            return await sensors.gather_observation()

    parts = asyncio.run(_run())
    labels = " ".join(_collect_text_labels(parts))

    assert "Local costmap" not in labels
    assert "SLAM map" in labels  # slam map renderer still populated


def test_gather_observation_omits_depth_when_renderer_returns_none():
    """If depth renderer returns None, no depth label is emitted but other channels still emit."""
    async def _run():
        with patch.object(sensors, "_render_rgb", new=AsyncMock(return_value=_fake_image_part())), \
             patch.object(sensors, "_render_depth", new=AsyncMock(return_value=None)), \
             patch.object(sensors, "_render_lidar", new=AsyncMock(return_value=_fake_image_part())), \
             patch.object(sensors, "_render_local_costmap", new=AsyncMock(return_value=_fake_image_part())), \
             patch.object(sensors, "_render_slam_map", new=AsyncMock(return_value=_fake_image_part())), \
             patch.object(sensors, "_render_state", new=AsyncMock(return_value=_fake_state_part())):
            return await sensors.gather_observation()

    parts = asyncio.run(_run())
    labels = " ".join(_collect_text_labels(parts))

    assert "Depth (OAK-D" not in labels, "depth label should be omitted when renderer returns None"
    assert "OAK-D fixed body camera" in labels, "RGB still emits"
    assert "LiDAR top-down" in labels, "LiDAR still emits"
    assert "Local costmap" in labels, "local costmap still emits"
    assert "SLAM map" in labels, "SLAM map still emits"
