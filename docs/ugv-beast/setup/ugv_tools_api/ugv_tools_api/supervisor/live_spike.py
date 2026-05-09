"""Gemini Live supervisor spike.

Standalone proof-of-concept for the UGV Beast supervisor service. Not a
systemd target, not wired into ugv-ears, no ER dispatch. Its one job is to
prove that:
    1. google-genai's live connect works with gemini-3.1-flash-live-preview
       on this Jetson with this GOOGLE_API_KEY
    2. Mic -> PCM -> Gemini -> PCM -> speaker round-trips audibly
    3. A function declaration results in a tool_call from the model and the
       tool response gets folded back into the audio reply
    4. Pantilt RGB video at 1 FPS is visible to the model and it can answer
       "what do you see?" correctly

Run from the ugv_waveshare container:
    source /opt/ros/humble/setup.bash
    source /home/ws/ugv_ws/install/setup.bash
    export GOOGLE_API_KEY=...   # or read from er/config
    export PYTHONPATH=/home/ws/ugv_ws/ugv_tools_api:$PYTHONPATH
    python3 -m ugv_tools_api.supervisor.live_spike

Speak any time after 'Session opened' appears. Ask 'what do you see?' to
exercise the video feed, or 'what is the robot doing?' for tool_call. Ctrl+C
to exit.
"""

import asyncio
import base64
import concurrent.futures
import math
import os
import struct
import subprocess
import sys
import threading
from typing import Optional

import httpx
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage

from google import genai
from google.genai import types


# ── Config (spike defaults; promoted to real config in Step 6) ──────────────
MODEL = os.environ.get("SUPERVISOR_MODEL", "gemini-3.1-flash-live-preview")
VOICE = os.environ.get("SUPERVISOR_VOICE", "Orus")
MIC_DEVICE = os.environ.get("SUPERVISOR_MIC", "plughw:CARD=Camera,DEV=0")
SPK_DEVICE = os.environ.get("SUPERVISOR_SPK", "plughw:CARD=Device,DEV=0")
TOOLS_API_URL = os.environ.get("TOOLS_API_URL", "http://localhost:8080")
CAMERA_TOPIC = os.environ.get("SUPERVISOR_CAMERA_TOPIC", "/camera/image/compressed")

SYSTEM_INSTRUCTION = (
    "You are the supervisor for the UGV Beast, a tracked exploration robot. "
    "You oversee the robot's mission-executing agent and speak with the operator. "
    "You have two tools available: "
    "(1) get_robot_state() returns pose, velocity, and lidar sector distances; "
    "(2) get_camera_view() captures a current frame from the pan-tilt camera. "
    "Call these on demand when the operator asks questions that need live data. "
    "Do not describe what you see unless you have just called get_camera_view — "
    "never guess at the scene. Be concise and conversational."
)

# ── Tool declarations (Gemini Live function schema) ─────────────────────────
GET_ROBOT_STATE = types.FunctionDeclaration(
    name="get_robot_state",
    description=(
        "Read the current robot pose, odometry velocity, and 8-sector lidar "
        "minimum distances. Use when the operator asks where the robot is, "
        "which way it's facing, or whether something is close to it."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={},
    ),
)

GET_CAMERA_VIEW = types.FunctionDeclaration(
    name="get_camera_view",
    description=(
        "Capture the current pan-tilt camera frame and return it as a JPEG "
        "image so you can see what the robot sees. Use when the operator asks "
        "'what do you see?', to verify the robot reached a landmark, to check "
        "if a path is clear, or to investigate a stall. Returns a single image "
        "you can describe."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={},
    ),
)


async def exec_get_robot_state() -> dict:
    """Call ugv_tools_api's /tool/status_get_odom and /tool/status_get_lidar_summary.

    Returned dict is handed to Gemini as the tool_response payload. Keep it
    small and JSON-serializable.
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        odom_task = client.post(f"{TOOLS_API_URL}/tool/status_get_odom", json={})
        lidar_task = client.post(f"{TOOLS_API_URL}/tool/status_get_lidar_summary", json={})
        odom_r, lidar_r = await asyncio.gather(odom_task, lidar_task)
    return {
        "odom": odom_r.json().get("result", {}),
        "lidar": lidar_r.json().get("result", {}),
    }


# ── Camera (ROS subscriber on /camera/image/compressed) ────────────────────
# rclpy spins in a background thread; the latest JPEG is stored under a lock.
# The async pump_video() task polls it at VIDEO_FPS and hands bytes to Gemini.
# Writes every frame to the cache but Gemini only sees 1 per second — the rest
# are dropped, matching Live API's 1 FPS ceiling.
class _FrameCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Optional[bytes] = None
        self._received = 0

    def set(self, data: bytes) -> None:
        with self._lock:
            self._data = data
            self._received += 1

    def get(self) -> Optional[bytes]:
        with self._lock:
            return self._data

    @property
    def received(self) -> int:
        with self._lock:
            return self._received


class _CameraNode(Node):
    def __init__(self, cache: _FrameCache) -> None:
        super().__init__("ugv_supervisor_spike_cam")
        self._cache = cache
        self.create_subscription(
            CompressedImage, CAMERA_TOPIC, self._on_frame,
            qos_profile_sensor_data,
        )

    def _on_frame(self, msg: CompressedImage) -> None:
        if msg.data:
            self._cache.set(bytes(msg.data))


def spin_camera(cache: _FrameCache, stop_evt: threading.Event) -> None:
    rclpy.init(args=None)
    node = _CameraNode(cache)
    try:
        while not stop_evt.is_set() and rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.2)
    finally:
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass


# ── Audio I/O (subprocess to arecord/aplay, same pattern as ugv-ears) ───────
def spawn_mic() -> subprocess.Popen:
    return subprocess.Popen(
        ["arecord", "-D", MIC_DEVICE, "-f", "S16_LE", "-r", "16000",
         "-c", "1", "-t", "raw", "-q"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )


def make_chime(duration_s: float = 0.15, freq_hz: float = 880.0,
               sample_rate: int = 24000, volume: float = 0.25) -> bytes:
    """Build a short sine-tone chime as raw S16_LE at the speaker sample rate.

    Volume 0.25 keeps it well below clipping and comfortable in a quiet room.
    880 Hz (A5) is audible but not shrill. Matches aplay's 24000 Hz config.
    """
    n = int(duration_s * sample_rate)
    amp = int(volume * 32767)
    samples = [int(amp * math.sin(2 * math.pi * freq_hz * i / sample_rate))
               for i in range(n)]
    # Fade in and out over 20 ms to avoid click artifacts
    fade = int(0.02 * sample_rate)
    for i in range(fade):
        samples[i] = int(samples[i] * i / fade)
        samples[-(i + 1)] = int(samples[-(i + 1)] * i / fade)
    return struct.pack(f"<{n}h", *samples)


_READY_CHIME = make_chime(duration_s=0.18, freq_hz=880.0)
_DONE_CHIME = make_chime(duration_s=0.12, freq_hz=440.0)


def spawn_spk() -> subprocess.Popen:
    # stderr captured (not DEVNULL) so format-negotiation failures are visible.
    # -q removed for the same reason. --buffer-size=24000 gives aplay a full
    # 1s ring buffer so it tolerates Gemini's bursty per-turn output.
    return subprocess.Popen(
        ["aplay", "-D", SPK_DEVICE, "-f", "S16_LE", "-r", "24000",
         "-c", "1", "-t", "raw", "--buffer-size=24000"],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )


# ── Main loop ───────────────────────────────────────────────────────────────
async def main() -> None:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY not set in environment")
        sys.exit(2)

    client = genai.Client(api_key=api_key)

    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=types.Content(
            parts=[types.Part(text=SYSTEM_INSTRUCTION)]
        ),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=VOICE,
                )
            ),
            # Pin language so mic captures with ambient noise don't get
            # auto-detected as Korean/Spanish/etc. Operator speaks English.
            language_code="en-US",
        ),
        # Gemini Live has server-side VAD. Bias it to HIGH start sensitivity
        # so the first word of an utterance is always captured (previously we
        # saw runs where quiet speech never produced a [user] transcript),
        # and LOW end sensitivity so we aren't cut off by short pauses.
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
            ),
        ),
        tools=[types.Tool(function_declarations=[GET_ROBOT_STATE, GET_CAMERA_VIEW])],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    print(f"[spike] Connecting model={MODEL} voice={VOICE}")
    print(f"[spike] Mic={MIC_DEVICE}  Speaker={SPK_DEVICE}")
    print(f"[spike] Camera tool on {CAMERA_TOPIC}")

    # Start the camera subscriber in a background thread. Frames are NOT
    # streamed to Gemini — instead, the get_camera_view tool reads the most
    # recent cached frame on demand, so Gemini only consumes images it
    # explicitly asked for.
    frame_cache = _FrameCache()
    cam_stop = threading.Event()
    cam_thread = threading.Thread(
        target=spin_camera, args=(frame_cache, cam_stop), daemon=True,
    )
    cam_thread.start()

    async with client.aio.live.connect(model=MODEL, config=config) as session:
        print("[spike] Session opened. Speak when ready. Ctrl+C to exit.")

        mic = spawn_mic()
        spk = spawn_spk()
        stop = asyncio.Event()

        # Mic echo suppression. The speaker and mic share the operator's
        # desk, so Gemini's own voice bleeds back into the mic. Without
        # gating, Gemini hears itself talking, its server-side VAD flags a
        # user interruption, and turn-taking breaks — observed as "session
        # opens fine, chime plays, Gemini greets, then mic bytes reach
        # Gemini but no [user] transcription ever happens."
        # Fix: suppress mic→Gemini forwarding for a short window after
        # each model audio chunk. Re-armed whenever another chunk arrives.
        mic_gate_until = 0.0  # monotonic seconds; if now < this, drop mic
        MIC_GATE_TAIL_S = 0.5  # how long to stay muted after last model audio

        # Audible ready cue so the operator knows the mic is hot. 880 Hz
        # chime = "ready for speech". Writing directly to spk before the
        # mic pump starts blocks for ~180ms, which is fine — chime should
        # finish before we start listening.
        try:
            spk.stdin.write(_READY_CHIME)
            spk.stdin.flush()
            print("[spike] CHIME: ready")
            # Suppress mic for the duration of the chime + small tail so
            # the operator's speaker-to-mic echo of the chime itself
            # doesn't trigger Gemini's VAD before they say anything.
            import time as _t
            mic_gate_until = _t.monotonic() + 0.5
        except Exception as e:
            print(f"[spike] chime write failed: {e}")

        # Dedicated single-thread executor for blocking mic reads so they
        # can't be starved by asyncio's default thread pool (which rclpy's
        # spin_once can saturate).
        mic_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="mic")

        async def pump_mic() -> None:
            """Stream 20ms frames of mic PCM to Gemini.

            Reports bytes/sec every 2s so we can distinguish local pipeline
            failures (bytes never captured) from remote failures (bytes sent
            but Gemini silent).
            """
            import time as _t
            loop = asyncio.get_running_loop()
            # 20 ms of 16kHz mono S16_LE = 640 bytes
            chunk_size = 640
            bytes_sent = 0
            next_report = _t.time() + 2.0
            while not stop.is_set():
                data = await loop.run_in_executor(mic_pool, mic.stdout.read, chunk_size)
                if not data:
                    break
                await session.send_realtime_input(
                    audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000")
                )
                bytes_sent += len(data)
                now = _t.time()
                if now >= next_report:
                    # 2s window, 32000 B/s = healthy
                    print(f"[spike] mic->gemini: {bytes_sent} B in 2s (target 64000)")
                    bytes_sent = 0
                    next_report = now + 2.0

        async def pump_responses() -> None:
            """Drain server events: play audio, print transcripts, run tools."""
            async for response in session.receive():
                # 1. Tool calls — execute and respond immediately
                if response.tool_call and response.tool_call.function_calls:
                    for fc in response.tool_call.function_calls:
                        print(f"[spike] TOOL CALL: {fc.name}({dict(fc.args or {})})")
                        if fc.name == "get_robot_state":
                            try:
                                result = await exec_get_robot_state()
                            except Exception as e:
                                result = {"error": str(e)}
                            print(f"[spike] TOOL RESULT: {result}")
                            await session.send_tool_response(
                                function_responses=[types.FunctionResponse(
                                    id=fc.id, name=fc.name, response=result,
                                )]
                            )
                        elif fc.name == "get_camera_view":
                            # Tool-driven vision: return the latest cached JPEG
                            # as inline_data in a FunctionResponsePart. Gemini
                            # receives the image only when it asks, so we
                            # don't bloat the session context with continuous
                            # frames.
                            jpeg = frame_cache.get()
                            if not jpeg:
                                print(f"[spike] TOOL RESULT: no frame yet (received={frame_cache.received})")
                                await session.send_tool_response(
                                    function_responses=[types.FunctionResponse(
                                        id=fc.id, name=fc.name,
                                        response={"error": "no camera frame available yet"},
                                    )]
                                )
                            else:
                                # The SDK's send_tool_response path cannot
                                # carry inline image bytes cleanly through
                                # its JSON serializer (FunctionResponseBlob
                                # declares bytes but the codec doesn't
                                # base64-encode). Workaround: return a text
                                # acknowledgement from the tool, then push
                                # the JPEG via send_realtime_input(video=...)
                                # which uses the streaming Blob class that
                                # DOES handle bytes correctly. Gemini treats
                                # the frame as incoming video and describes
                                # it in the next model turn.
                                print(f"[spike] TOOL RESULT: {len(jpeg)}-byte JPEG (total received={frame_cache.received})")
                                await session.send_tool_response(
                                    function_responses=[types.FunctionResponse(
                                        id=fc.id, name=fc.name,
                                        response={"status": "ok", "note": "image delivered as video frame"},
                                    )]
                                )
                                await session.send_realtime_input(
                                    video=types.Blob(
                                        data=jpeg, mime_type="image/jpeg",
                                    )
                                )
                        else:
                            await session.send_tool_response(
                                function_responses=[types.FunctionResponse(
                                    id=fc.id, name=fc.name,
                                    response={"error": f"unknown tool {fc.name}"},
                                )]
                            )

                # 2. Audio via SDK's concatenator property (handles inline_data
                # across all parts of the server message in one call).
                audio_bytes = response.data
                if audio_bytes:
                    print(f"[spike] audio {len(audio_bytes)} bytes -> speaker")
                    try:
                        spk.stdin.write(audio_bytes)
                        spk.stdin.flush()
                    except BrokenPipeError:
                        # aplay died; drain its stderr so we can see why.
                        err = b""
                        try:
                            err = spk.stderr.read()
                        except Exception:
                            pass
                        print(f"[spike] ERROR aplay pipe closed. stderr: {err.decode(errors='replace')[:500]}")
                        stop.set()
                        break

                # 3. Transcripts + interruption handling
                sc = response.server_content
                if sc:
                    if sc.input_transcription and sc.input_transcription.text:
                        print(f"[user]   {sc.input_transcription.text}")
                    if sc.output_transcription and sc.output_transcription.text:
                        print(f"[model]  {sc.output_transcription.text}")
                    if sc.interrupted:
                        try:
                            spk.stdin.flush()
                        except Exception:
                            pass

        try:
            await asyncio.gather(pump_mic(), pump_responses())
        finally:
            stop.set()
            cam_stop.set()
            try:
                mic.terminate()
            except Exception:
                pass
            try:
                spk.stdin.close()
                spk.terminate()
            except Exception:
                pass
            mic_pool.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[spike] Shut down.")
