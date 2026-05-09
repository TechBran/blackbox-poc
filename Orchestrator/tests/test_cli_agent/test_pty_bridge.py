import asyncio
import pytest
from Orchestrator.cli_agent.pty_bridge import PtyBridge


@pytest.mark.asyncio
async def test_echoes_typed_bytes():
    bridge = PtyBridge.spawn(["bash", "--noprofile", "--norc", "-c",
                              "while read line; do echo got:$line; done"])
    try:
        await bridge.write(b"hello\n")
        out = b""
        for _ in range(20):
            chunk = await bridge.read(timeout=0.5)
            if chunk:
                out += chunk
            if b"got:hello" in out:
                break
        assert b"got:hello" in out
    finally:
        bridge.close()


@pytest.mark.asyncio
async def test_resize_no_throw():
    bridge = PtyBridge.spawn(["bash", "--noprofile", "--norc", "-c", "sleep 5"])
    try:
        bridge.resize(cols=120, rows=40)
    finally:
        bridge.close()


@pytest.mark.asyncio
async def test_close_terminates_child():
    bridge = PtyBridge.spawn(["bash", "--noprofile", "--norc", "-c", "sleep 60"])
    bridge.close()
    await asyncio.sleep(1)
    assert not bridge.isalive()
