"""Gemini Live session controller.

Owns the WebSocket, wires mic/speaker/video to model I/O, dispatches tool
calls, persists resumption handles, and reconnects on GoAway. Long-lived:
runs until the supervisor service terminates.

Google-recommended patterns applied (per https://ai.google.dev/gemini-api/docs/live-session):
  1. context_window_compression with SlidingWindow removes duration cap
  2. session_resumption handle persisted across restarts (2-hr TTL)
  3. GoAway event triggers proactive reconnect
  4. language_code pinned prevents auto-detect hallucination
  5. AutomaticActivityDetection tuned for operator mic environment

Empirical regression guards (from the spike, 2026-04-20):
  - Use response.data for audio out (NOT manual part iteration)
  - Mic reads via MicStream.pool (NOT default asyncio executor)
  - FunctionResponseBlob can't carry bytes through the SDK; use
    send_realtime_input(video=...) sidechannel for image-returning tools
  - Play 880Hz chime on session open so operator knows mic is hot
  - Speaker writes must dispatch via run_in_executor — SpeakerStream.write
    blocks on ALSA ring-buffer backpressure (1s at 24kHz) and would stall
    the event loop if awaited directly

Production-hardening (post-compile-review):
  - Exponential backoff on the reconnect loop so a revoked key can't
    burn token quota in a tight restart cycle. Resets on any session
    that ran long enough to be considered healthy.
  - Subsystem startup is transactional: if spk.start() raises after
    mic.start() succeeds, the already-started subsystems are cleanly
    stopped. Python's try/finally only runs finally if the try was
    entered, so we have to start inside the try.
  - pump_mic signals session_stop on EOF/exit in a finally block so
    pump_responses doesn't silently keep running with a dead mic.
"""
import asyncio
import base64
import os
from typing import Optional

# Historical note: this module previously installed a `_patch_ws_keepalive()`
# monkeypatch on `websockets.asyncio.client.connect` to raise the default
# 20 s ping_interval/ping_timeout to 60 s. With the raw-WS port (Task 4),
# `raw_session.connect_raw_live` passes ping_interval=/ping_timeout= directly
# into `websockets.connect(...)` from cfg.ws_ping_*_s, so the monkeypatch
# was dead code. Removed in Task 8.

from .config import SupervisorConfig
from .handle_store import HandleStore
from .audio_io import MicStream, SpeakerStream, make_chime
from .video_io import RosCamera
from .tool_declarations import all_tools_json
from .raw_session import build_setup_payload, connect_raw_live
from . import tool_handlers as th


class _AudioSetupFailed(Exception):
    """Sentinel raised inside run()'s session-setup block when mic or
    speaker fails to start. Caught by the immediately-outer except so
    the finally block cleans up and the outer wake loop moves on."""


_READY_CHIME = make_chime()
# Distinct lower-pitch tone for session close so the operator can tell
# "session ended" from "session opened" by ear. 660 Hz (E5) vs. ready's
# 880 Hz (A5); longer 300 ms for a clearer "goodbye" feel.
_CLOSE_CHIME = make_chime(duration_s=0.3, freq_hz=660.0)

# Reconnect backoff. Caps the retry rate when a persistent failure
# (e.g., revoked GOOGLE_API_KEY) would otherwise hammer Google's API.
# Sessions that run long enough to be considered healthy reset backoff
# to the initial value so one isolated bad session doesn't punish us.
_BACKOFF_INITIAL = 2.0
_BACKOFF_MAX = 60.0
_BACKOFF_FACTOR = 2.0
_HEALTHY_SESSION_S = 30.0

# Coordination file with ugv-ears: while present, ears releases its PyAudio
# input stream so the supervisor can open arecord on the same USB mic. Only
# one process holds the ALSA hardware at a time. Overridable for tests.
_MUTE_FLAG_PATH = os.environ.get("MUTE_FLAG_PATH", "/tmp/ugv_ears_muted")
# Delay between writing the mute flag and opening arecord, giving ears'
# poll loop time to notice the flag and close its PyAudio stream.
# Empirically ears' PyAudio -> ALSA teardown takes ~3 s on this
# container (the kernel USB driver holds the device open briefly after
# userspace close() returns; PaAlsa logs show the same EBUSY pattern
# when ears itself reacquires). 2 s here + MicStream's retry loop
# (5 × 1 s) gives us a combined ~7 s window before giving up.
_MIC_HANDOFF_DELAY_S = 2.0

# Auto-close the Gemini Live session after this many seconds with no
# meaningful activity (no user transcription, no model response, no tool
# call). Default 10 minutes. Arecord streams audio continuously, so we
# can't use "bytes on the wire" as the activity signal — we use Gemini's
# server-side VAD outcomes (transcripts, responses) instead. Overridable
# via SUPERVISOR_IDLE_TIMEOUT_S.
_IDLE_TIMEOUT_S = float(os.environ.get("SUPERVISOR_IDLE_TIMEOUT_S", "600.0"))
# How often the idle watchdog wakes to check the timer. Finer resolution
# would just burn CPU; 10 s is plenty for a 600 s timeout.
_IDLE_CHECK_INTERVAL_S = 10.0


_SYSTEM_PROMPT = (
    "You are the supervisor for the UGV Beast, a small tracked exploration robot. "
    "You converse with the operator by voice and can command the robot's on-device "
    "execution agent (ER) via the dispatch_er_mission tool. ER handles one complete "
    "task per invocation (drive here, map this room, etc.) and returns control to "
    "you when done. dispatch_er_mission is asynchronous: it returns immediately "
    "and you may keep talking while the robot moves. While a mission is running, "
    "you AUTOMATICALLY receive 1 Hz pose updates and (if watch mode is on) 1 FPS "
    "pantilt frames as part of the same dispatch tool call — do NOT call "
    "get_robot_state or get_camera_view mid-mission to re-fetch data you are "
    "already being shown; just refer to the streaming updates. Watch mode turns "
    "on automatically when you dispatch a mission and off when all missions "
    "terminate; use set_watch_mode only when the operator asks to override. "
    "Outside of an active mission, get_robot_state and get_camera_view remain "
    "the right tools for spot-checking. get_er_mission_status, get_costmap_view, "
    "cancel_er_mission, and emergency_stop are also available. Be concise and "
    "conversational. Never describe or guess at the robot's surroundings unless "
    "you have captured an image (via get_camera_view OR via the mission's "
    "watch-mode stream) in this session.\n\n"
    "Your senses. You receive a continuous ambient video feed from the pan-tilt "
    "camera — one frame every ~3 seconds. This is your eyes. Treat it like a "
    "passenger watching the world go by from the robot's vantage: comment on "
    "what changes and what's interesting (\"a chair just came into view\", "
    "\"we're approaching a doorway\", \"the lighting dropped\"), but do NOT "
    "narrate every frame — only when something is worth saying. Call "
    "get_camera_view to push a fresher pantilt frame between watch ticks when "
    "you want a sharper look at something specific; the image arrives on the "
    "same turn via the realtime perception channel (the tool result itself is "
    "just a small JSON ack, NOT the bytes). Call get_slam_map_view to fetch a "
    "top-down floor plan with your current position marked — use it for cross-"
    "room reasoning (\"is there a room I haven't been to?\", \"which way is "
    "the kitchen from here?\"); on-demand only, the map only changes when "
    "SLAM updates it. The pan-tilt gimbal is yours alone via gimbal_look_at; "
    "ER does not have it. Pan and tilt freely — ER will rotate the body if "
    "it needs a viewpoint the gimbal can't reach. ER 1.6 is the body and "
    "reflexes (it plans and executes nav goals); you are the eyes and voice. "
    "When the operator gives a mission, dispatch_er_mission to ER and narrate "
    "what you see as ER drives."
)


def _build_setup_payload_from_cfg(cfg: SupervisorConfig, resume_handle: Optional[str]) -> dict:
    """Returns the JSON dict to send as the first WS message after connect.

    Tool JSON conversion is delegated to tool_declarations.all_tools_json()
    so this module doesn't reach into pydantic internals — keeps the
    SDK-shape detail behind a public helper.

    Deliberate departures from the prior _build_config (encoded in
    raw_session.build_setup_payload):
      * NO inputAudioTranscription / outputAudioTranscription
        (suspected cycling-bug trigger; transcripts are silent until
        we re-enable in a follow-up — log lines may stay quiet).
      * NO realtimeInputConfig (default VAD works fine in the
        Orchestrator's non-phone path).
    """
    return build_setup_payload(
        model=cfg.model,
        voice=cfg.voice,
        system_instruction=_SYSTEM_PROMPT,
        tools=all_tools_json(),
        resume_handle=resume_handle,
    )


class Supervisor:
    """Composes mic, speaker, ROS camera, tool handlers, and the Gemini
    Live session into one long-running controller.

    Lifecycle: construct, await run(), which loops session after session
    (reconnecting on GoAway or error) until stop() is called. The outer
    loop also re-spawns the session entirely on exception, with
    exponential backoff, so transient network or SDK issues don't kill
    the service and persistent auth failures don't burn quota.
    """

    def __init__(self, cfg: SupervisorConfig) -> None:
        self._cfg = cfg
        self._handle_store = HandleStore(cfg.handle_store_path)
        # Note: no SDK client object — connect_raw_live takes the api_key
        # directly inside _run_one_session.
        # Set True at the start of each wake cycle so the FIRST
        # _run_one_session in the cycle asks Gemini to greet the
        # operator. Subsequent reconnects (GoAway) within the same
        # wake cycle see False and stay silent — we don't want a new
        # "hi" every 25 s. Cleared after the greeting fires.
        self._greeting_pending: bool = False
        # Mic and speaker are created per-session: MicStream.stop() is
        # terminal (thread pool shutdown is one-shot) and SpeakerStream
        # owns an aplay subprocess. Fresh instances each wake keeps the
        # lifecycle crisp.
        self._mic: Optional[MicStream] = None
        self._spk: Optional[SpeakerStream] = None
        self._cam = RosCamera(cfg.camera_topic, cfg.costmap_topic)
        self._tracker = th.MissionTracker()
        # Three-event state machine:
        #   _stop              full service shutdown (lifespan SIGTERM)
        #   _session_requested wake word fired → open Gemini Live session
        #   _close_session     /stop_session fired → end current session,
        #                      return to wake-wait (DOES NOT stop service)
        self._stop = asyncio.Event()
        self._session_requested = asyncio.Event()
        self._close_session = asyncio.Event()

    @property
    def tracker(self) -> th.MissionTracker:
        return self._tracker

    @property
    def active(self) -> bool:
        """True while a Gemini Live session is running (mic is held)."""
        return self._mic is not None

    async def run(self) -> None:
        """Wake-gated session loop.

        Idle: ROS camera runs; mic/speaker are NOT open; ears owns the
        USB mic for wake-word detection. Waits on _session_requested.

        On wake: write MUTE_FLAG so ears releases its PyAudio stream,
        wait for handoff, open arecord + aplay, enter the inner reconnect
        loop. The inner loop persists across GoAway via session_resumption
        until /stop_session (close_session) or /full-shutdown (stop) fires.

        After close: stop mic + speaker, clear MUTE_FLAG, return to idle.
        ears reacquires the mic and resumes wake-word listening.

        The ROS camera stays hot across sessions — its bring-up is
        heavy and it has no ALSA conflict with ears.
        """
        loop = asyncio.get_running_loop()

        # Fresh process = fresh conversation. Gemini Live handles are
        # valid for 2 hr after session end, and resuming days-old
        # handles manifests as "timed out during opening handshake".
        # More importantly, the operator's expectation is: reboot the
        # robot, get a new conversation. Within-session reconnects
        # (GoAway, network blips) still use the handle because
        # SessionResumptionUpdate events arrive during the session and
        # HandleStore.set() writes them back to disk live — next
        # _run_one_session in the same process reads that fresh handle.
        self._handle_store.clear()
        print("[supervisor] cleared stale session_resumption handle (fresh process)")

        try:
            self._cam.start()
        except Exception as e:
            print(f"[supervisor] camera failed to start: {e}")

        try:
            while not self._stop.is_set():
                print("[supervisor] idle — waiting for wake word (/open_session)")
                await self._session_requested.wait()
                if self._stop.is_set():
                    break
                # Consume both events so a stale signal doesn't immediately
                # re-trigger or close.
                self._session_requested.clear()
                self._close_session.clear()

                # Greet the operator on this wake cycle's FIRST session
                # open (not on subsequent GoAway reconnects). Cleared
                # inside _run_one_session once the greeting request is
                # actually sent to Gemini.
                self._greeting_pending = True

                # --- Mic handoff: ears → supervisor -----------------------
                self._set_mute_flag()
                await asyncio.sleep(_MIC_HANDOFF_DELAY_S)

                mic_ok = spk_ok = False
                try:
                    self._mic = MicStream(self._cfg)
                    self._spk = SpeakerStream(self._cfg)
                    # MicStream.start() can raise if arecord fails every
                    # retry (device busy, USB unplugged). SpeakerStream
                    # is simpler and rarely fails. If either fails, we
                    # log + fall through to the `finally` below which
                    # clears MUTE_FLAG so ears reacquires the mic — the
                    # session just silently never opens. The outer
                    # wake loop continues so the operator can try again.
                    try:
                        self._mic.start(); mic_ok = True
                        self._spk.start(); spk_ok = True
                    except Exception as e:
                        print(
                            f"[supervisor] audio setup failed, aborting "
                            f"this wake cycle: {e}"
                        )
                        # Skip the rest of the try body by raising a
                        # sentinel that the outer except swallows.
                        raise _AudioSetupFailed() from e

                    # Play the ready chime ONCE per wake cycle, right
                    # after the speaker comes up and BEFORE the inner
                    # reconnect loop — so GoAway reconnects inside the
                    # inner loop don't replay the chime over Gemini's
                    # voice. SpeakerStream.write blocks on ALSA
                    # backpressure; dispatch via run_in_executor.
                    try:
                        await loop.run_in_executor(None, self._spk.write, _READY_CHIME)
                    except Exception as e:
                        print(f"[supervisor] warn: ready chime write failed: {e}")

                    # Inner reconnect loop: stays up across GoAway so the
                    # live session feels persistent to the operator.
                    # Behavioral change vs. the SDK port: reconnect ONLY
                    # on explicit goAway. Iterator-end-without-goAway
                    # exits to wake-wait (cycling-bug regression guard:
                    # prior behavior reopened immediately, causing 43
                    # sessions in 2 minutes when native-audio closed
                    # after every turn_complete).
                    await self._inner_reconnect_loop()
                except _AudioSetupFailed:
                    # Sentinel raised from within the try above so
                    # we fall through to finally cleanly. Outer loop
                    # resumes wake-wait after cleanup.
                    pass
                finally:
                    # --- Mic handoff: supervisor → ears -------------------
                    # Order matters: stop mic FIRST so the close chime
                    # can't echo back into arecord and confuse Gemini's
                    # final transcription. Then play the close chime
                    # (distinct lower-pitch tone) through the speaker
                    # that's still open. Then stop the speaker.
                    if mic_ok and self._mic is not None:
                        try: self._mic.stop()
                        except Exception: pass
                    if spk_ok and self._spk is not None:
                        try:
                            await loop.run_in_executor(
                                None, self._spk.write, _CLOSE_CHIME,
                            )
                            # Let ALSA actually render the chime before
                            # we terminate aplay. _CLOSE_CHIME is 300 ms;
                            # 400 ms gives aplay's 1 s ring buffer time
                            # to flush without a click cutoff.
                            await asyncio.sleep(0.4)
                        except Exception as e:
                            print(f"[supervisor] warn: close chime failed: {e}")
                        try: self._spk.stop()
                        except Exception: pass
                    self._mic = None
                    self._spk = None
                    self._clear_mute_flag()
                    # Fresh session on next wake. Within-wake reconnects
                    # (GoAway) still use the handle because it gets
                    # re-written live by the SessionResumptionUpdate
                    # events inside the inner loop. Clearing here only
                    # fires at the END of a wake cycle, so the next
                    # `/open_session` gets a brand-new Gemini session.
                    self._handle_store.clear()
                    print("[supervisor] session closed — returning to wake-wait")
        finally:
            try:
                self._cam.stop()
            except Exception:
                pass
            # Safety net: never leave a stale mute flag behind on service
            # shutdown. Without this, ears would stay silent through a
            # restart gap until someone noticed.
            self._clear_mute_flag()

    def request_session(self) -> None:
        """Trigger a Gemini Live session to open. Idempotent; safe to call
        while a session is already running (event is just consumed again
        on the next idle cycle)."""
        self._session_requested.set()

    def request_close_session(self) -> None:
        """End the current Gemini Live session and return to wake-wait.
        Service stays up. Next wake word opens a fresh session."""
        self._close_session.set()

    def request_stop(self) -> None:
        """Signal full service shutdown. Wakes the outer idle loop and
        ends any active session so shutdown doesn't block on a 15-minute
        Gemini session timer."""
        self._stop.set()
        # Unblock any in-flight waits so the run loop exits promptly.
        self._session_requested.set()
        self._close_session.set()

    # ── Mute flag helpers ────────────────────────────────────────────────
    #
    # The mute flag is the atomic handoff primitive between ears (PyAudio)
    # and supervisor (arecord). Both processes respect the same file path
    # (env: MUTE_FLAG_PATH). Best-effort: if the flag write fails we log
    # and continue — ears will retry; we don't block session open on it.

    def _set_mute_flag(self) -> None:
        try:
            with open(_MUTE_FLAG_PATH, "w") as f:
                f.write("supervisor\n")
        except Exception as e:
            print(f"[supervisor] warn: could not write {_MUTE_FLAG_PATH}: {e}")

    def _clear_mute_flag(self) -> None:
        try:
            os.remove(_MUTE_FLAG_PATH)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[supervisor] warn: could not remove {_MUTE_FLAG_PATH}: {e}")

    async def _inner_reconnect_loop(self) -> None:
        """Within-wake reconnect loop: opens a Live session, and on
        return inspects ``should_reconnect`` to decide whether to reopen
        (goAway path) or break to wake-wait (natural-end path).

        Extracted from ``run()`` so this branching is unit-testable in
        isolation. The cycling-bug regression guard (Task 5) locks down
        the goAway-only behavior — see tests/supervisor/test_session.py.

        The cycling-bug regression guard: prior to the 2026-04-25 raw-WS
        port, the SDK's aio.live iterator would naturally end on every
        server-side turn_complete (no goAway warning), and this loop
        would immediately reopen the session. On
        gemini-2.5-flash-native-audio-latest this produced 43 sessions
        in 2 minutes during operator speech, with each new session
        starting from cold-start VAD context. The current implementation
        breaks to wake-wait on natural-iterator-end and ONLY reconnects
        when an explicit goAway event is observed.

        Behavior contract:
          * ``_run_one_session`` returns with ``should_reconnect=[True]``
            -> ``continue`` (loop iterates to open a fresh session via
            the persisted resumption handle). This is the goAway path.
          * ``_run_one_session`` returns with ``should_reconnect=[False]``
            -> ``break`` (loop exits, caller returns to wake-wait). This
            is the cycling-bug regression guard.
          * ``_run_one_session`` raises -> backoff with interruptable
            wait on ``_close_session``; retry until stop/close fires
            or the next session returns cleanly.
        """
        loop = asyncio.get_running_loop()
        backoff = _BACKOFF_INITIAL
        while not self._stop.is_set() and not self._close_session.is_set():
            should_reconnect = [False]
            t0 = loop.time()
            try:
                await self._run_one_session(should_reconnect)
            except Exception as e:
                if loop.time() - t0 > _HEALTHY_SESSION_S:
                    backoff = _BACKOFF_INITIAL
                print(
                    f"[supervisor] session error, reconnect in "
                    f"{backoff:.1f}s: {e}"
                )
                # Sleep with interrupt: if /stop_session fires
                # during backoff, exit immediately instead of
                # sitting on a cold event loop.
                try:
                    await asyncio.wait_for(
                        self._close_session.wait(), timeout=backoff,
                    )
                    break
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * _BACKOFF_FACTOR, _BACKOFF_MAX)
                continue
            # Successful return — distinguish goAway-driven
            # (reconnect) from natural-end (close).
            if should_reconnect[0]:
                backoff = _BACKOFF_INITIAL
                continue  # outer loop reopens via handle_store
            else:
                # Server closed without goAway — treat as
                # session-end. Exit to wake-wait.
                break

    async def _run_one_session(self, should_reconnect: list) -> None:
        """Open one Live session, pump I/O until GoAway or local stop.

        ``should_reconnect`` is a one-element list used as a mutable
        wrapper to communicate goAway state up to the outer reconnect
        loop in run(). Set to ``[True]`` if the server emitted a GoAway
        during this session — the outer loop will reopen via the
        persisted session_resumption handle. Otherwise iterator-end is
        treated as a session-end and the outer loop returns to wake-wait
        (cycling-bug regression guard).
        """
        # ── Diagnostic: session counter + lifetime of previous session
        if not hasattr(self, "_session_counter"):
            self._session_counter = 0
            self._last_session_end_t: Optional[float] = None
        self._session_counter += 1
        sess_n = self._session_counter
        loop_pre = asyncio.get_running_loop()
        prev_lifetime_s = (
            None if self._last_session_end_t is None
            else loop_pre.time() - self._last_session_end_t
        )
        if prev_lifetime_s is not None:
            print(f"[diag] session #{sess_n} opening (gap since last close: {prev_lifetime_s:.2f}s)")
        else:
            print(f"[diag] session #{sess_n} opening (first this wake)")

        setup = _build_setup_payload_from_cfg(self._cfg, self._handle_store.get())
        sess_open_t = loop_pre.time()
        async with connect_raw_live(
            url=self._cfg.gemini_live_url,
            api_key=self._cfg.google_api_key,
            setup_payload=setup,
            ping_interval_s=self._cfg.ws_ping_interval_s,
            ping_timeout_s=self._cfg.ws_ping_timeout_s,
        ) as session:
            print("[supervisor] session opened (raw-WS)")

            # NOTE: ready chime intentionally NOT played here. The chime
            # is played once per wake cycle in run() so that GoAway
            # reconnects (which are frequent on the Live 3.1 preview)
            # don't interrupt Gemini's voice with a repeating beep.
            loop = asyncio.get_running_loop()

            # Greeting prompt deliberately disabled.
            # Why: on `gemini-3.1-flash-live-preview`, first-token
            # latency after a client_content greeting is ~80 s. Combined
            # with ActivityHandling.NO_INTERRUPTION (set below), any
            # operator speech during that 80 s window is silently
            # dropped because the model is still "producing" the
            # greeting turn. That made the whole session feel dead.
            # Removing the greeting lets the model respond directly to
            # the operator's first utterance — fast first-token to
            # real content. Cue that the mic is hot comes from the
            # 880 Hz ready chime played in run() before this function.
            if self._greeting_pending:
                self._greeting_pending = False
                print("[supervisor] greeting skipped (preview-friendly)")

            session_stop = asyncio.Event()
            # Idle watchdog: updated whenever Gemini sends us anything
            # meaningful (audio, text, transcript, tool call). Reset on
            # session open so the timer starts fresh after each wake.
            last_activity = loop.time()

            def mark_active() -> None:
                nonlocal last_activity
                last_activity = loop.time()

            async def pump_mic():
                # 20 ms of 16kHz mono S16_LE = 640 bytes per chunk.
                chunk = int(self._cfg.input_sample_rate * self._cfg.chunk_ms / 1000) * 2
                # Pre-built silent frame for the half-duplex gate.
                # We send zeros (not "skip") so Gemini's server-side VAD
                # keeps its timing reference — a gap would make it think
                # the stream ended. Silence is treated as non-activity.
                silent_chunk = b"\x00" * chunk

                # AEC initialization. If aec_mode=="aec3" succeeds we use
                # WebRTC AEC3 (or speexdsp on aarch64) to subtract the
                # speaker reference from the mic, restoring barge-in. If
                # init fails (no backend available, etc.) we transparently
                # fall back to the legacy half-duplex gate. Setting
                # SUPERVISOR_AEC_MODE=halfduplex env-side forces the
                # legacy path even if the backend is healthy — a 5-second
                # rollback path with no rebuild.
                from .aec import Aec3Wrapper
                from .audio_io import resample_24k_to_16k
                aec: Optional[Aec3Wrapper] = None
                if self._cfg.aec_mode == "aec3":
                    try:
                        aec = Aec3Wrapper(
                            sample_rate_hz=self._cfg.input_sample_rate, frame_ms=10,
                        )
                        print("[supervisor] AEC3 enabled")
                    except Exception as e:
                        print(
                            f"[supervisor] AEC3 init failed ({type(e).__name__}: {e}); "
                            f"falling back to halfduplex for this session"
                        )
                        aec = None
                elif self._cfg.aec_mode == "passthrough":
                    # Hardware AEC on the speaker device (e.g., EMEET
                    # OfficeCore conferencing speakerphone) handles echo
                    # cancellation. Skip software AEC AND the half-duplex
                    # mute gate so the operator can barge-in mid-sentence.
                    # The device's mic stream is already echo-cancelled by
                    # the hardware DSP before it reaches arecord.
                    print("[supervisor] AEC mode=passthrough (hardware AEC on device, full-duplex)")
                else:
                    print(f"[supervisor] AEC mode={self._cfg.aec_mode} (halfduplex)")
                # The AEC backend works in 10 ms frames (160 samples =
                # 320 bytes at 16 kHz). Mic capture chunks are 20 ms.
                # Process two AEC frames per mic chunk and concatenate.
                aec_frame_bytes = int(self._cfg.input_sample_rate * 0.010) * 2
                # Defensive: AEC backends raise on non-standard frame
                # sizes, which would trigger the per-frame except → halfduplex
                # path on every chunk. Bail to halfduplex up-front if the
                # mic chunk size isn't a clean multiple of the AEC frame
                # so the mode swap is logged once instead of N-times-per-second.
                if aec is not None and chunk % aec_frame_bytes != 0:
                    print(
                        f"[supervisor] mic chunk {chunk} not divisible by "
                        f"aec_frame_bytes {aec_frame_bytes}; falling back to halfduplex"
                    )
                    aec = None
                # Equivalent reference chunk size at the speaker rate
                # (24 kHz vs mic 16 kHz). int(0.020 * 24000) * 2 = 960 bytes.
                ref_chunk_bytes_24k = int(chunk * self._cfg.output_sample_rate
                                          / self._cfg.input_sample_rate)

                # ── Diagnostic counters (logged every 5s + on exit)
                _diag_mic_chunks_sent = 0
                _diag_mic_bytes_sent = 0
                _diag_aec_chunks = 0
                _diag_halfduplex_chunks = 0
                _diag_passthrough_chunks = 0
                _diag_aec_failures = 0
                _diag_last_log_t = loop.time()

                try:
                    while not session_stop.is_set():
                        data = await loop.run_in_executor(
                            self._mic.pool, self._mic.read_chunk, chunk,
                        )
                        if not data:
                            # arecord EOF — subprocess died. Terminal per
                            # MicStream's contract. Print AND return so
                            # the finally-block's session_stop.set() also
                            # wakes pump_responses from its draining loop.
                            print("[supervisor] mic EOF — arecord died, ending session")
                            return

                        if aec is not None:
                            # Pull a time-aligned reference chunk from the
                            # speaker ring at 24 kHz, then resample to 16 kHz
                            # to match the mic. Pad/truncate to exactly
                            # match the mic chunk length so the AEC sees
                            # frame-aligned input.
                            now = loop.time()
                            ref_24k = (
                                self._spk.reference_ring.read_aligned(
                                    now=now, size_bytes=ref_chunk_bytes_24k,
                                    delay_ms=self._cfg.aec_delay_ms,
                                )
                                if self._spk is not None
                                else b"\x00" * ref_chunk_bytes_24k
                            )
                            ref_16k = resample_24k_to_16k(ref_24k)
                            if len(ref_16k) < chunk:
                                ref_16k = ref_16k + b"\x00" * (chunk - len(ref_16k))
                            elif len(ref_16k) > chunk:
                                ref_16k = ref_16k[:chunk]

                            try:
                                # Process per-10ms-frame so the backend
                                # sees its native frame size. data and
                                # ref_16k are both `chunk` bytes (20 ms);
                                # split into two 10 ms halves.
                                out_frames = []
                                for off in range(0, chunk, aec_frame_bytes):
                                    out_frames.append(aec.process(
                                        data[off:off + aec_frame_bytes],
                                        ref_16k[off:off + aec_frame_bytes],
                                    ))
                                outgoing = b"".join(out_frames)
                            except Exception as e:
                                print(
                                    f"[supervisor] AEC process failed "
                                    f"({type(e).__name__}: {e}); falling back "
                                    f"to halfduplex for remainder of this session"
                                )
                                aec = None
                                _diag_aec_failures += 1
                                outgoing = silent_chunk if (
                                    self._spk is not None and self._spk.is_playing()
                                ) else data
                        elif self._cfg.aec_mode == "passthrough":
                            # Hardware AEC mode (e.g., EMEET conferencing
                            # speakerphone). The device's mic ADC already
                            # delivers echo-cancelled audio, so just pass
                            # through. No software gate needed; full-duplex
                            # barge-in works because the model's voice
                            # never reaches the mic stream.
                            outgoing = data
                        else:
                            # Half-duplex fallback (regression guard #7):
                            # client-side anti-echo gate. While the robot
                            # is talking through its own speaker, send
                            # silence upstream so Gemini doesn't hear its
                            # own voice echoed back through the mic.
                            outgoing = silent_chunk if (
                                self._spk is not None and self._spk.is_playing()
                            ) else data
                        await session.send_realtime_input(
                            audio=outgoing,
                            mime_type="audio/pcm;rate=16000",
                        )
                        # Charge the token budget: outgoing chunk
                        # represents chunk_ms of upstream audio whether
                        # we sent the AEC-cancelled signal, half-duplex
                        # silence, or raw mic. Gemini still bills for it.
                        budget.audio_seconds(self._cfg.chunk_ms / 1000.0)

                        # ── Diagnostic: periodic mic upload stats
                        _diag_mic_chunks_sent += 1
                        _diag_mic_bytes_sent += len(outgoing)
                        if outgoing == silent_chunk:
                            _diag_halfduplex_chunks += 1
                        elif aec is not None:
                            _diag_aec_chunks += 1
                        else:
                            _diag_passthrough_chunks += 1
                        _diag_now = loop.time()
                        if _diag_now - _diag_last_log_t >= 5.0:
                            elapsed = _diag_now - _diag_last_log_t
                            print(
                                f"[diag] mic upload sess#{sess_n}: "
                                f"{_diag_mic_chunks_sent} chunks, "
                                f"{_diag_mic_bytes_sent/elapsed:.0f} B/s "
                                f"(aec={_diag_aec_chunks}, halfduplex={_diag_halfduplex_chunks}, "
                                f"passthrough={_diag_passthrough_chunks}, aec_failures={_diag_aec_failures})"
                            )
                            _diag_mic_chunks_sent = 0
                            _diag_mic_bytes_sent = 0
                            _diag_aec_chunks = 0
                            _diag_halfduplex_chunks = 0
                            _diag_passthrough_chunks = 0
                            _diag_last_log_t = _diag_now
                finally:
                    # Any exit path — clean return, exception, whatever —
                    # wakes pump_responses out of its async for loop on
                    # the next session_stop check. Without this, a dead
                    # mic leaves pump_responses draining Gemini forever.
                    print(f"[diag] pump_mic exiting sess#{sess_n} "
                          f"(final: aec_failures={_diag_aec_failures})")
                    session_stop.set()

            async def pump_responses():
                # ── Diagnostic: receive event counter + exit reason
                _diag_responses_received = 0
                _diag_audio_bytes_received = 0
                _exit_reason = "unknown"
                try:
                    async for event in session.receive():
                        _diag_responses_received += 1

                        # 1. Tool calls — JSON shape: event["toolCall"]["functionCalls"]
                        tc = event.get("toolCall")
                        if tc and tc.get("functionCalls"):
                            function_calls = tc["functionCalls"]
                            mark_active()
                            await self._dispatch_tool_calls(
                                session, function_calls,
                                spawn_poller=_spawn_poller,
                                poller_tasks=poller_tasks,
                                pollers=pollers,
                                watch=watch,
                                operator_override=operator_override,
                                send_video=_send_video,
                            )
                            continue

                        # 2. serverContent — audio + transcripts + turnComplete
                        sc = event.get("serverContent")
                        if sc:
                            # Audio: concatenate inlineData bytes from
                            # modelTurn.parts (matches the SDK's
                            # response.data semantics). NOTE:
                            # gemini-2.5-flash-native-audio-* responses
                            # may include 'text' and 'thought' parts
                            # alongside inlineData; the audio filter
                            # ignores them, which is what we want — they
                            # are the model's reasoning trace, not
                            # user-facing output.
                            model_turn = sc.get("modelTurn", {}) or {}
                            audio_chunks: list = []
                            for part in model_turn.get("parts", []) or []:
                                inline = part.get("inlineData") or {}
                                mt = inline.get("mimeType", "")
                                if "audio" in mt and inline.get("data"):
                                    audio_chunks.append(base64.b64decode(inline["data"]))
                            if audio_chunks:
                                mark_active()
                                audio = b"".join(audio_chunks)
                                _diag_audio_bytes_received += len(audio)
                                # write is blocking on ALSA backpressure
                                await loop.run_in_executor(None, self._spk.write, audio)
                                # Charge the token budget for downstream audio.
                                # 24 kHz S16 mono = 48000 bytes/sec → len/48000
                                # is the audio-second equivalent.
                                budget.audio_seconds(len(audio) / 48000.0)

                            # Transcripts (for logging only). NOTE: the
                            # raw-WS setup_payload deliberately omits
                            # inputAudioTranscription /
                            # outputAudioTranscription configs (suspected
                            # cycling-bug trigger — see raw_session.py).
                            # As a result, sc.get("inputTranscription")
                            # / sc.get("outputTranscription") will be
                            # ABSENT and the [user]/[model] log lines
                            # may be silent until we re-enable in a
                            # follow-up. Keep the print code defensively
                            # (None-guarded) so it runs if the API later
                            # starts emitting them.
                            in_t = sc.get("inputTranscription")
                            if in_t and in_t.get("text"):
                                mark_active()
                                print(f"[user]  {in_t['text']}")
                            out_t = sc.get("outputTranscription")
                            if out_t and out_t.get("text"):
                                mark_active()
                                print(f"[model] {out_t['text']}")

                            # Diagnostic: turn_complete signals end of a model
                            # response turn. If the session is closing right
                            # after this, that's our smoking gun.
                            if sc.get("turnComplete"):
                                print(f"[diag] sess#{sess_n} server_content.turn_complete=True "
                                      f"(received so far: {_diag_responses_received})")

                        # 3. Google-recommended lifecycle events
                        upd = event.get("sessionResumptionUpdate")
                        if upd and upd.get("newHandle"):
                            self._handle_store.set(upd["newHandle"])

                        if "goAway" in event:
                            # IMPORTANT: do NOT return here. GoAway means
                            # "the server will close in `time_left` seconds";
                            # it is NOT a terminal event. Bailing early cuts
                            # off the in-flight audio response mid-word and
                            # the operator hears the model chop off. The
                            # correct behavior is to KEEP pumping responses
                            # (including any remaining audio bytes for the
                            # current turn) until the server actually closes
                            # the connection, at which point the async for
                            # iterator ends and _run_one_session returns
                            # naturally. The outer reconnect loop then
                            # resumes the session via the persisted
                            # session_resumption handle. Flag the outer
                            # loop so it knows to reopen.
                            left = (event.get("goAway") or {}).get("timeLeft")
                            print(
                                f"[supervisor] GoAway received, time_left={left}; "
                                f"continuing until server closes (session_resumption "
                                f"will restore on reconnect)"
                            )
                            should_reconnect[0] = True

                        if (session_stop.is_set()
                                or self._stop.is_set()
                                or self._close_session.is_set()):
                            _exit_reason = "stop-event"
                            return
                    # async for ended naturally — websocket closed from the
                    # server with no more events. Distinguish goAway-driven
                    # close (outer loop will reopen) from natural session
                    # end (outer loop returns to wake-wait — this is the
                    # cycling-bug regression guard).
                    _exit_reason = (
                        "natural-iterator-end (goAway-driven, will reconnect)"
                        if should_reconnect[0]
                        else "natural-iterator-end (no goAway, session ended)"
                    )
                except Exception as e:
                    _exit_reason = f"exception: {type(e).__name__}: {e}"
                    raise
                finally:
                    print(f"[diag] pump_responses sess#{sess_n} exit reason={_exit_reason} "
                          f"(received={_diag_responses_received}, "
                          f"audio_bytes={_diag_audio_bytes_received})")

            async def watch_external_close():
                """Wake when /stop_session or full shutdown fires and
                propagate the signal by setting session_stop.

                Without this, pump_responses is blocked inside
                `async for response in session.receive()` when Gemini is
                silent, and we'd have to wait for the next server message
                to even notice the close request. With this watchdog, we
                cancel the pumps immediately; the async-with block tears
                down the WebSocket cleanly.
                """
                stop_t = asyncio.create_task(self._stop.wait())
                close_t = asyncio.create_task(self._close_session.wait())
                try:
                    await asyncio.wait(
                        {stop_t, close_t},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    stop_t.cancel()
                    close_t.cancel()
                session_stop.set()

            async def watch_idle_timeout():
                """Auto-close the session after _IDLE_TIMEOUT_S seconds
                with no meaningful activity. Polls every
                _IDLE_CHECK_INTERVAL_S seconds so we never oversleep
                past the deadline by more than one check window.

                Triggers _close_session rather than session_stop so the
                outer reconnect loop also exits — an idle session
                shouldn't silently reconnect and keep burning tokens.
                """
                while not session_stop.is_set():
                    elapsed = loop.time() - last_activity
                    if elapsed >= _IDLE_TIMEOUT_S:
                        print(
                            f"[supervisor] idle {elapsed:.0f}s "
                            f"(>{_IDLE_TIMEOUT_S:.0f}s) — auto-closing session"
                        )
                        self._close_session.set()
                        session_stop.set()
                        return
                    # Sleep until the timer could next fire or one
                    # check-interval, whichever is sooner. If activity
                    # arrives during the sleep, elapsed resets on the
                    # next loop iteration.
                    await asyncio.sleep(min(
                        _IDLE_CHECK_INTERVAL_S,
                        _IDLE_TIMEOUT_S - elapsed,
                    ))

            # Per-session mission progress pollers, keyed by fc.id of the
            # originating dispatch_er_mission tool_call. Spawned by the
            # dispatch handler on success; cancelled by cancel_er_mission,
            # emergency_stop, and (below) session teardown. Defined here
            # so its lifecycle is bound to this session — a fresh
            # _run_one_session gets a fresh dict.
            poller_tasks: dict[str, asyncio.Task] = {}
            # Parallel dict of MissionPoller instances so we can call
            # cooperative .cancel() on them (which emits a terminal
            # "cancelled" FunctionResponse before exiting). Hard
            # task.cancel() in the finally teardown is the fallback for
            # session shutdown.
            pollers: dict[str, "object"] = {}

            # Token-budget proactive rotation. Estimates context usage
            # from audio seconds (input + output) + JPEG frames pushed
            # by watch mode. When usage crosses the threshold, the
            # watch_budget watchdog sets session_stop so the inner
            # reconnect loop opens a fresh session via the persisted
            # session_resumption handle. Initial estimates live in
            # budget.py and are refined by the Task 8 bench session.
            from .budget import TokenBudget
            budget = TokenBudget(
                window_tokens=self._cfg.budget_window_tokens,
                threshold=self._cfg.budget_threshold,
            )

            async def watch_budget():
                """Polls the TokenBudget every 2 s. When usage crosses the
                threshold, triggers session_stop so the inner reconnect loop
                opens a fresh session (carrying conversation context via
                the persisted session_resumption handle)."""
                while not session_stop.is_set():
                    try:
                        await asyncio.wait_for(session_stop.wait(), timeout=2.0)
                        return
                    except asyncio.TimeoutError:
                        pass
                    if budget.should_rotate:
                        print(
                            f"[supervisor] budget rotate: usage={budget.usage_pct:.1%} "
                            f"({budget.used_tokens:.0f}/{self._cfg.budget_window_tokens}); "
                            f"closing session for resumption-handle reconnect"
                        )
                        session_stop.set()
                        return

            # Watch mode: ambient 1 FPS pantilt JPEG push.
            # Default off; auto-on during dispatch_er_mission success;
            # auto-off on terminal mission completion when no operator
            # override is set. operator_override is wrapped in a list so
            # nested lambdas can read its latest value (closure scoping:
            # nonlocal int wouldn't propagate cleanly through lambdas).
            from .watch_stream import WatchStream
            from . import config as cfg_mod

            # send_video callback — MUST be async def, NOT lambda. The
            # callback type is Callable[..., Awaitable[None]] and a
            # `lambda` returning a coroutine is technically Awaitable-
            # shaped but fragile (forgetting `await` on either side
            # silently drops the send). async def captures `session`
            # via closure and gives a clean coroutine.
            async def _send_video(jpeg, mime_type):
                await session.send_realtime_input(video=jpeg, mime_type=mime_type)

            watch = WatchStream(
                # FPS sourced from config.WATCH_FPS (default 0.33 = one
                # frame every ~3 s). Operator-tunable via the WATCH_FPS
                # env var, or at runtime via set_watch_mode(fps=...).
                # Tuned so Gemini Live has continuous embodied vision
                # without burning the context window — at ~50 KB/frame,
                # 0.33 FPS = ~1 MB/min, well inside the budget rotation
                # threshold. Google rejects pushes faster than 1 FPS.
                self._cam, fps=cfg_mod.WATCH_FPS,
                on_frame=lambda: budget.jpeg_frames(1),
                send_video=_send_video,
            )
            operator_override: list = [None]
            # Default-on at session creation (T1 of the embodied-observer
            # plan). Mirror the dispatch_er_mission auto-on guard: only
            # flip if no operator override is in flight. operator_override
            # is None at session start, so this always fires unless
            # WATCH_DEFAULT_ON is False; the guard is here to make the
            # invariant explicit alongside its sibling at the dispatch
            # site.
            if cfg_mod.WATCH_DEFAULT_ON and operator_override[0] is None:
                watch.set(on=True, source="pantilt")

            def _make_callbacks(fc_id: str, fc_name: str):
                from .mission_poller import PollerCallbacks
                async def _send(body: dict, *, will_continue: bool, scheduling: str) -> None:
                    await session.send_tool_response(function_responses=[{
                        "id": fc_id, "name": fc_name, "response": body,
                        "will_continue": will_continue, "scheduling": scheduling,
                    }])
                async def _terminal(body: dict) -> None:
                    await _send(body, will_continue=False, scheduling="WHEN_IDLE")
                    # Auto-off watch on terminal mission, unless operator
                    # has explicitly overridden the watch state. If a
                    # second mission is in flight (pollers dict still has
                    # entries for other missions), leave watch on.
                    #
                    # Eventual-consistency caveat: when this runs, the
                    # poller's done_callback (added in _spawn_poller) may
                    # not have removed pollers[fc_id] yet, so `not pollers`
                    # may evaluate False and skip the auto-off. That's
                    # harmless — the next dispatch's auto-on path resets
                    # the state, and the cost is at most a few extra
                    # ambient frames during the dispatch gap. Do NOT
                    # "fix" this with a synchronization point; the
                    # frame cost is trivial vs. the lock complexity.
                    if operator_override[0] is None and not pollers:
                        watch.set(on=False, source="pantilt")
                return PollerCallbacks(
                    send_silent=lambda b: _send(b, will_continue=True, scheduling="SILENT"),
                    send_when_idle=lambda b: _send(b, will_continue=True, scheduling="WHEN_IDLE"),
                    send_terminal=_terminal,
                )

            async def _spawn_poller(fc_id: str, fc_name: str, mission_id: str) -> None:
                from .mission_poller import MissionPoller
                cb = _make_callbacks(fc_id, fc_name)
                p = MissionPoller(self._cfg, fc_id=fc_id, fc_name=fc_name,
                                  mission_id=mission_id, callbacks=cb)
                pollers[fc_id] = p
                task = asyncio.create_task(p.run())
                # Clean up dict entries when the poller finishes naturally
                # (terminal emitted, run() returned). Without this, a 24-hr
                # session with one mission/min accretes ~1500 dead entries.
                # Re-pop is safe in cancel paths: pollers.pop(fc_id, None)
                # returns None if already removed.
                task.add_done_callback(
                    lambda _t, k=fc_id: (pollers.pop(k, None), poller_tasks.pop(k, None))
                )
                poller_tasks[fc_id] = task

            mic_task = asyncio.create_task(pump_mic())
            resp_task = asyncio.create_task(pump_responses())
            watch_task = asyncio.create_task(watch_external_close())
            idle_task = asyncio.create_task(watch_idle_timeout())
            # NOTE: watch_stream_task name disambiguates from watch_task
            # (which is watch_external_close). "watch" overloaded here:
            # one is the close-signal watcher, the other is the camera
            # push loop.
            watch_stream_task = asyncio.create_task(watch.run_with_callback())
            budget_task = asyncio.create_task(watch_budget())
            try:
                # Run until any of the six exits. In the common "user
                # said /stop_session" case, watch_task completes first,
                # sets session_stop, and we cancel the other tasks below.
                # If nobody speaks for 10 minutes, idle_task trips first.
                # If estimated tokens cross threshold, budget_task trips.
                # watch_stream_task only exits on explicit watch.stop()
                # in the finally block.
                await asyncio.wait(
                    {mic_task, resp_task, watch_task, idle_task,
                     watch_stream_task, budget_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                # Stop watch stream cooperatively before hard-cancelling
                # so the loop exits cleanly via its asyncio.wait_for path.
                watch.stop()
                for t in (mic_task, resp_task, watch_task, idle_task,
                          watch_stream_task, budget_task):
                    if not t.done():
                        t.cancel()
                # Also cancel any in-flight pollers — they're per-session
                for t in poller_tasks.values():
                    if not t.done():
                        t.cancel()
                # Wait for cancellations to settle (existing pattern,
                # extended to include pollers and watch_stream_task)
                for t in (mic_task, resp_task, watch_task, idle_task,
                          watch_stream_task, budget_task,
                          *poller_tasks.values()):
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        # Swallow tail errors during teardown; the outer
                        # reconnect loop will classify and log on retry.
                        pass

    # ── Tool dispatch ────────────────────────────────────────────────────

    async def _dispatch_tool_calls(
        self, session, function_calls,
        *, spawn_poller=None, poller_tasks=None, pollers=None,
        watch=None, operator_override=None, send_video=None,
    ) -> None:
        """Map Gemini tool_call events to async handlers.

        Every handler returns a JSON-serializable dict. The vision tool
        get_camera_view pushes its JPEG via the realtime_input channel
        (same path WatchStream uses) and returns a tiny ack — placing
        the bytes on the channel Gemini's vision encoder actually
        consumes, instead of the function_response data path Gemini
        only acknowledges. The legacy two-step _vision_tool path
        remains for get_costmap_view (no equivalent migration yet).

        Sequential (not gathered): later calls may depend on earlier
        side effects (e.g. emergency_stop must complete before any
        subsequent dispatch). Gemini rarely sends >1 call per response
        in practice.

        spawn_poller / poller_tasks are session-scoped closures injected
        by _run_one_session. dispatch_er_mission spawns a per-mission
        progress poller via spawn_poller; cancel_er_mission and
        emergency_stop cancel any in-flight pollers. They're optional so
        the method remains testable in isolation.

        send_video is the same realtime-input push callable threaded
        into WatchStream by _run_one_session. Optional so unit tests
        of _dispatch_tool_calls can omit it for tools that don't push.
        """
        for fc in function_calls:
            name = fc["name"]
            args = dict(fc.get("args") or {})
            print(f"[supervisor] tool_call {name}({args})")

            if name == "get_robot_state":
                resp = await th.exec_get_robot_state(self._cfg)
                await self._respond(session, fc, resp)
            elif name == "get_camera_view":
                # T2 of embodied-observer plan: push the JPEG via
                # realtime_input (same channel WatchStream uses) and
                # return a tiny ack. Function_response inline_data is
                # "data-to-acknowledge" (two-prompt lag); realtime_input
                # is "perception" the vision encoder reasons about on
                # the same turn. send_video is the same callable
                # threaded into WatchStream by _run_one_session.
                if send_video is None:
                    await self._respond(session, fc, {
                        "error": "get_camera_view requires send_video; "
                                 "session not fully initialized",
                    })
                else:
                    resp = await th.exec_get_camera_view(self._cam, send_video)
                    await self._respond(session, fc, resp)
            elif name == "get_slam_map_view":
                # T3 of embodied-observer plan: on-demand SLAM map push.
                # Same realtime_input channel as get_camera_view (T2) so
                # Gemini's vision encoder reasons about the rasterized
                # map on the same turn. The model decides cadence —
                # there's no auto-on or polling here, mirroring the
                # plan's "tool-driven, not pushed" design.
                if send_video is None:
                    await self._respond(session, fc, {
                        "error": "get_slam_map_view requires send_video; "
                                 "session not fully initialized",
                    })
                else:
                    resp = await th.exec_get_slam_map_view(self._cam, send_video)
                    await self._respond(session, fc, resp)
            elif name == "get_costmap_view":
                await self._vision_tool(
                    session, fc, self._cam.get_costmap_png(), "image/png", "costmap",
                )
            elif name == "dispatch_er_mission":
                # NON_BLOCKING: emit an immediate SILENT placeholder so the
                # model keeps generating audio/text. Spawn a per-mission
                # progress-streaming poller under the same fc.id with
                # 1 Hz SILENT pose ticks, WHEN_IDLE state-transition
                # events, and a terminal will_continue=False on
                # completed/failed/aborted/cancelled.
                #
                # If the dispatch HTTP call fails (resp has 'error'), we
                # MUST send a terminal will_continue=False right here —
                # otherwise the model is left waiting forever for a
                # follow-up that will never come.
                resp = await th.exec_dispatch_er_mission(self._cfg, self._tracker, **args)
                await session.send_tool_response(
                    function_responses=[{
                        "id": fc["id"], "name": fc["name"],
                        "response": resp,
                        "will_continue": True,
                        "scheduling": "SILENT",
                    }],
                )
                if "error" in resp:
                    # Dispatch failed — send terminal so the model isn't
                    # left waiting for streaming progress that won't come.
                    await session.send_tool_response(
                        function_responses=[{
                            "id": fc["id"], "name": fc["name"],
                            "response": resp,
                            "will_continue": False,
                            "scheduling": "WHEN_IDLE",
                        }],
                    )
                else:
                    # Dispatch succeeded — spawn the progress streamer
                    # under this fc.id so streamed updates bind to the
                    # same tool call.
                    mid = resp.get("mission_id")
                    if mid:
                        # Auto-on watch mode unless operator has explicitly
                        # forced a state via set_watch_mode this session.
                        if (watch is not None and operator_override is not None
                                and operator_override[0] is None):
                            watch.set(on=True, source="pantilt")
                        if spawn_poller is not None:
                            await spawn_poller(fc["id"], fc["name"], mid)
            elif name == "cancel_er_mission":
                resp = await th.exec_cancel_er_mission(self._cfg, self._tracker, **args)
                # Cooperatively cancel any in-flight pollers — there's
                # only one mission at a time but iterate to be safe.
                # poller.cancel() sets an Event that the run() loop
                # observes on its next iteration, then emits a terminal
                # "cancelled" FunctionResponse before exiting.
                if pollers is not None:
                    for p in list(pollers.values()):
                        p.cancel()
                await self._respond(session, fc, resp)
            elif name == "get_er_mission_status":
                resp = await th.exec_get_er_mission_status(self._cfg, self._tracker)
                await self._respond(session, fc, resp)
            elif name == "emergency_stop":
                resp = await th.exec_emergency_stop(self._cfg)
                # Emergency stop kills any in-flight mission too.
                if pollers is not None:
                    for p in list(pollers.values()):
                        p.cancel()
                await self._respond(session, fc, resp)
            elif name == "lights_on":
                resp = await th.exec_lights(self._cfg, on=True, **args)
                await self._respond(session, fc, resp)
            elif name == "lights_off":
                resp = await th.exec_lights(self._cfg, on=False, **args)
                await self._respond(session, fc, resp)
            elif name == "gimbal_look_at":
                resp = await th.exec_gimbal_look_at(self._cfg, **args)
                await self._respond(session, fc, resp)
            elif name == "set_watch_mode":
                resp = await th.exec_set_watch_mode(**args)
                # Operator has now explicitly forced the watch state;
                # subsequent mission auto-on/off no-ops until end of
                # session (operator_override stays sticky).
                if operator_override is not None:
                    operator_override[0] = bool(resp["watch_on"])
                if watch is not None:
                    watch.set(on=bool(resp["watch_on"]), source=resp["source"])
                    # Optional FPS retune (clamped in the handler).
                    # Apply only when the operator explicitly passed
                    # fps — otherwise leave the existing cadence alone.
                    if resp.get("fps") is not None:
                        watch.set_period(float(resp["fps"]))
                await self._respond(session, fc, resp)
            else:
                await self._respond(session, fc, {"error": f"unknown tool {name}"})

    async def _respond(self, session, fc, resp: dict) -> None:
        await session.send_tool_response(
            function_responses=[
                {"id": fc["id"], "name": fc["name"], "response": resp},
            ],
        )

    async def _vision_tool(self, session, fc, payload: Optional[bytes], mime: str, label: str) -> None:
        """Return an image from a tool call.

        Two-step pattern: acknowledge the tool with a JSON response,
        then push the image via send_realtime_input(video=...) so
        Gemini treats the frame as incoming video and describes it in
        the next model turn. (FunctionResponse JSON can't reliably carry
        raw bytes on Live preview models.)
        """
        if not payload:
            await self._respond(session, fc, {"error": f"no {label} frame available"})
            return
        await self._respond(session, fc, {
            "status": "ok",
            "note": f"{label} image delivered as video frame",
        })
        await session.send_realtime_input(video=payload, mime_type=mime)
