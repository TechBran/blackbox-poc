"""Smoke tests for the supervisor's _SYSTEM_PROMPT.

The Embodied Observer plan (T4) adds a senses block teaching Gemini Live
to narrate proactively, advertises the new get_slam_map_view tool, and
clarifies the gimbal/body split with ER. These tests pin the substantive
nudges so a future edit can't silently delete them.

Sync tests only — no pytest-asyncio dependency. The prompt is a module-
level string constant, so importing the module is enough.
"""


def _prompt() -> str:
    from ugv_tools_api.supervisor.session import _SYSTEM_PROMPT
    return _SYSTEM_PROMPT


# ── New tool advertised ────────────────────────────────────────────────

def test_prompt_advertises_get_slam_map_view():
    """The new SLAM-map tool must be named in the prompt so Gemini Live
    knows it exists and what it's for. Tool declarations alone are not
    enough — the model needs the prose hint to call it at the right
    moment (cross-room reasoning).
    """
    assert "get_slam_map_view" in _prompt()


# ── Ambient-feed / proactive-narration nudge ───────────────────────────

def test_prompt_describes_ambient_video_feed():
    """The prompt must communicate that a video frame arrives passively
    (~3 s cadence) so the model treats the feed as eyes, not as data
    awaiting acknowledgement. Accept any of the canonical phrasings the
    plan suggested — pinning the exact wording would be too brittle.
    """
    p = _prompt()
    assert "every ~3 seconds" in p or "every 3 seconds" in p or "ambient" in p.lower()


def test_prompt_encourages_proactive_narration():
    """The 'passenger' framing (or equivalent) is the load-bearing nudge
    that turns the default 'respond when prompted' posture into running
    commentary. Without it, default-on watch mode is wasted context.
    """
    p = _prompt().lower()
    assert "passenger" in p or "narrate" in p or "comment on what" in p


# ── Gimbal-vs-ER ownership split ──────────────────────────────────────

def test_prompt_clarifies_gimbal_ownership():
    """Gemini Live owns the pan-tilt gimbal exclusively; ER does not.
    The prompt should make that crisp so the model doesn't try to
    delegate gimbal moves through dispatch_er_mission.
    """
    p = _prompt()
    # Either side of the split being mentioned is sufficient; both
    # would be ideal. Use a permissive OR so a tone polish doesn't
    # snap the test.
    assert ("ER does not" in p and "gimbal" in p.lower()) or "gimbal is yours" in p.lower()


# ── Body/mind split with ER ────────────────────────────────────────────

def test_prompt_describes_body_mind_split_with_er():
    """ER 1.6 is the body/reflexes; Gemini Live is the eyes/voice.
    The prompt must communicate this so the model narrates rather
    than tries to plan motion itself.
    """
    p = _prompt().lower()
    assert "body" in p and ("eyes" in p or "voice" in p or "narrate" in p)


# ── Stale-claim guard: get_camera_view no longer returns the image ─────

def test_prompt_does_not_claim_get_camera_view_returns_image_data():
    """Post-T2, get_camera_view pushes the JPEG via realtime_input and
    returns only a small JSON ack. A stale prompt that says the tool
    'returns the image' would mislead the model into the old two-prompt
    'I received it / now describe it' lag pattern. Guard against that
    regression.
    """
    p = _prompt().lower()
    # Permissive: we don't ban the words "return" or "image" anywhere in
    # the prompt (other tools may legitimately return data). We just
    # assert the prompt doesn't pair them with get_camera_view in a way
    # that asserts it returns image bytes.
    forbidden_phrases = (
        "get_camera_view returns the image",
        "get_camera_view returns image",
        "get_camera_view will return",
    )
    for phrase in forbidden_phrases:
        assert phrase not in p, f"prompt still claims {phrase!r}"
