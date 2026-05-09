"""Pulse routing smoke test (container-side).

Confirms the ugv_waveshare container can talk to the host's system-mode
pulseaudio daemon via either a bind-mounted unix socket or a TCP loopback
listener. Read-only: queries `pactl info` and `pactl list short
sources/sinks` — never opens an audio stream.

Skipped on hosts without PULSE_SERVER set (i.e. local laptop dev). Run
inside the container after the recreate: pactl + PULSE_SERVER env must
be set. Accepts both `unix:/run/pulse/native` (legacy bind-mount) and
`tcp:127.0.0.1:<port>` (TCP loopback pivot).
"""
import os
import shutil
import subprocess
import pytest


def _pulse_env_present() -> bool:
    server = os.environ.get("PULSE_SERVER", "")
    return (
        (server.startswith("unix:") or server.startswith("tcp:"))
        and shutil.which("pactl") is not None
    )


pytestmark = pytest.mark.skipif(
    not _pulse_env_present(),
    reason="PULSE_SERVER not set or pactl unavailable (run inside container)",
)


def test_pactl_info_responds():
    """pactl info should return a server name within 5 seconds."""
    out = subprocess.check_output(
        ["pactl", "info"], stderr=subprocess.STDOUT, timeout=5,
    ).decode("ascii", errors="replace")
    assert "Server Name" in out, f"pactl info missing Server Name: {out[:300]}"


def test_emeet_source_visible():
    """EMEET should appear in `pactl list short sources` output.
    System pulse on this Jetson exposes it as alsa_input.usb-EMEET_*."""
    out = subprocess.check_output(
        ["pactl", "list", "short", "sources"], stderr=subprocess.STDOUT, timeout=5,
    ).decode("ascii", errors="replace")
    assert "EMEET" in out or "OfficeCore" in out, (
        f"EMEET source not found in pactl output: {out[:500]}"
    )


def test_emeet_sink_visible():
    """EMEET should appear in `pactl list short sinks` output."""
    out = subprocess.check_output(
        ["pactl", "list", "short", "sinks"], stderr=subprocess.STDOUT, timeout=5,
    ).decode("ascii", errors="replace")
    assert "EMEET" in out or "OfficeCore" in out, (
        f"EMEET sink not found in pactl output: {out[:500]}"
    )
