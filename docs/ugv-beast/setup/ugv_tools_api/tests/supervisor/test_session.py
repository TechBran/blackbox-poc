"""Regression tests for the supervisor session module after the raw-WS port.

Task 4 swaps the google.genai SDK transport for a hand-crafted
RawLiveSession over `websockets`. These tests guard against the SDK
sneaking back in.

Task 5 locks down the goAway-only reconnect behavior of the inner
reconnect loop. The cycling bug (43 sessions in 2 minutes during
operator speech with the 2.5-native-audio model) was caused by the
prior inner-loop behavior of immediately reopening on iterator-end
even when no goAway was emitted. The new contract: reconnect ONLY on
goAway. Iterator-end without goAway breaks back to wake-wait. These
tests fail loudly if a future refactor accidentally re-introduces the
unconditional-continue behavior.
"""
import asyncio

import pytest


def test_session_module_does_not_import_google_genai_live_at_module_level():
    """The raw-WS port should remove direct dependence on google.genai for
    transport. types.Blob and FunctionResponse references must be gone.
    """
    import ugv_tools_api.supervisor.session as sess_mod
    src = open(sess_mod.__file__).read()
    assert "from google.genai" not in src
    assert "from google import genai" not in src
    assert "client.aio.live.connect" not in src


# ── Inner reconnect loop regression guards (Task 5) ────────────────────────
#
# We construct a Supervisor via __new__ so we can wire the minimum state
# needed by _inner_reconnect_loop: the two stop events plus a stub for
# _run_one_session. The mic/speaker/camera/handle-store state is not
# touched by _inner_reconnect_loop — it only calls _run_one_session and
# inspects should_reconnect[0] — so the bypass is safe and well-scoped.


def _make_bare_supervisor():
    """Build a Supervisor with just the events _inner_reconnect_loop needs.

    Bypasses __init__ so we don't drag in HandleStore, RosCamera, mic
    config, etc. _inner_reconnect_loop only touches:
      * self._stop (asyncio.Event)
      * self._close_session (asyncio.Event)
      * self._run_one_session (stubbed per-test)

    Intentionally minimal: if a future change to _inner_reconnect_loop
    starts touching mic/spk/cam/handle_store/cfg, these tests will fail
    with AttributeError — that's the signal to update the helper AND
    review whether the new touch is in-scope for the inner loop's contract.
    """
    from ugv_tools_api.supervisor.session import Supervisor
    sup = Supervisor.__new__(Supervisor)
    sup._stop = asyncio.Event()
    sup._close_session = asyncio.Event()
    return sup


@pytest.mark.asyncio
async def test_inner_reconnect_loop_reopens_on_goAway():
    """When _run_one_session returns with should_reconnect=[True],
    the outer loop continues — opens a fresh session via the
    persisted resumption handle. This is the goAway-driven
    reconnect path.
    """
    sup = _make_bare_supervisor()
    call_count = 0

    async def stub_run_one_session(should_reconnect):
        nonlocal call_count
        call_count += 1
        # First call: goAway path — outer loop must iterate.
        # Second call: natural-end — outer loop must break.
        if call_count == 1:
            should_reconnect[0] = True
        else:
            should_reconnect[0] = False

    sup._run_one_session = stub_run_one_session

    await sup._inner_reconnect_loop()

    assert call_count == 2, (
        f"expected goAway -> reconnect (2 _run_one_session calls), "
        f"got {call_count}"
    )
    # _close_session should not have been touched by the loop —
    # this is goAway-driven, not user-initiated.
    assert not sup._close_session.is_set()
    assert not sup._stop.is_set()


@pytest.mark.asyncio
async def test_inner_reconnect_loop_breaks_on_natural_end_no_goAway():
    """When _run_one_session returns with should_reconnect=[False],
    the outer loop breaks immediately. This is the cycling-bug
    regression guard: the prior behavior reopened immediately,
    causing 43 sessions in 2 minutes during operator speech with
    the 2.5-native-audio model.

    If a future refactor accidentally re-introduces a `continue`
    instead of `break` in the False branch, this test will hang
    forever (call_count grows without bound) — pytest-asyncio's
    default timeout will surface the failure.
    """
    sup = _make_bare_supervisor()
    call_count = 0

    async def stub_run_one_session(should_reconnect):
        nonlocal call_count
        call_count += 1
        # Iterator-end without goAway — outer loop MUST break.
        # If the regression slips back in, call_count will keep
        # growing and we'll either hit a sentinel or hang.
        should_reconnect[0] = False
        # Defensive: if the loop ignored should_reconnect[0] and
        # kept calling, fail fast on the second call rather than
        # spin forever.
        if call_count > 1:
            raise AssertionError(
                "inner_reconnect_loop kept iterating after "
                "should_reconnect[0]=False — cycling-bug "
                "regression has been re-introduced"
            )

    sup._run_one_session = stub_run_one_session

    await sup._inner_reconnect_loop()

    assert call_count == 1, (
        f"expected natural-end -> break (1 _run_one_session call), "
        f"got {call_count} — cycling-bug regression guard tripped"
    )


@pytest.mark.asyncio
async def test_inner_reconnect_loop_retries_with_backoff_on_exception(monkeypatch):
    """When _run_one_session raises, the loop retries with
    exponential backoff. After enough retries it gives up only
    when _close_session fires (or when stop fires).

    We patch asyncio.wait_for in the session module so the
    backoff sleep doesn't actually block 2+ seconds. The patched
    version raises asyncio.TimeoutError immediately (matching
    the production "no close_session signal during backoff"
    behavior), letting the loop retry on the next iteration.
    """
    sup = _make_bare_supervisor()
    call_count = 0

    async def stub_run_one_session(should_reconnect):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call raises — loop should backoff and retry.
            raise RuntimeError("transport blip")
        else:
            # Second call succeeds with natural-end — loop breaks.
            should_reconnect[0] = False

    sup._run_one_session = stub_run_one_session

    # Fast-forward the backoff sleep. The production path is
    # `await asyncio.wait_for(self._close_session.wait(), timeout=backoff)`
    # — a TimeoutError means "no close signal during backoff,
    # retry now". Patching the session module's bound asyncio
    # ensures we don't accidentally break asyncio.wait_for for
    # the rest of the test runner.
    import ugv_tools_api.supervisor.session as sess_mod
    real_wait_for = sess_mod.asyncio.wait_for

    async def fast_wait_for(awaitable, timeout):
        # Cancel the inner coroutine so it doesn't leak, then
        # raise TimeoutError to mimic backoff-elapsed-without-close.
        if asyncio.iscoroutine(awaitable):
            awaitable.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(sess_mod.asyncio, "wait_for", fast_wait_for)
    try:
        await sup._inner_reconnect_loop()
    finally:
        monkeypatch.setattr(sess_mod.asyncio, "wait_for", real_wait_for)

    assert call_count == 2, (
        f"expected exception -> backoff -> retry -> break (2 calls), "
        f"got {call_count}"
    )
