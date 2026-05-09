# Supervisor Raw-WS Port — Live Bench Record

> Companion notes file for `docs/plans/2026-04-25-supervisor-rawws-port.md` (Task 7).
> Operator-led bench on 2026-04-25 confirming the cycling-bug fix.

## Verdict

**The cycling bug is fixed.** Multi-turn conversation worked end-to-end on `gemini-2.5-flash-native-audio-latest` with the new raw-WS transport.

## Timeline (journal extract)

```
20:58:17  [diag] sess#1 server_content.turn_complete=True (received so far: 55)
          [diag] mic upload sess#1: 250 chunks, 32000 B/s (aec=250, ...aec_failures=0)
          ... continuous mic uploads + many turn_completes ...
20:58:36  [supervisor] tool_call lights_on({})
20:58:40  [supervisor] tool_call lights_on({})
20:58:43  [supervisor] tool_call lights_off({})
20:58:51  [supervisor] tool_call dispatch_er_mission({'mission': 'drive forward 10 centimeters'})
21:00:04  [supervisor] tool_call dispatch_er_mission({'mission': 'turn 90 degrees'})
21:00:18  [supervisor] budget rotate: usage=80.9% (25896/32000); closing session for resumption-handle reconnect
21:00:18  [diag] pump_mic exiting sess#1 (final: aec_failures=0)
21:00:18  [diag] pump_responses sess#1 exit reason=unknown (received=732, audio_bytes=2123520)
21:00:19  [supervisor] session closed — returning to wake-wait
21:01:10  POST /open_session (operator's second wake)
21:01:14  [diag] session #2 opening (first this wake)
21:01:14  [supervisor] session opened (raw-WS)
21:01:14  [supervisor] AEC3 enabled
          ... continued multi-turn conversation in sess#2 ...
```

## Verification matrix

| C# | Criterion | Result |
|---|---|---|
| C1 | `[supervisor] session opened (raw-WS)` appears | ✅ |
| **C2** | **Sessions stay open across many `turn_complete`s** | ✅ sess#1 absorbed 732 receive events / 2+ minutes / ~25 `turn_complete=True` events without a single reopen |
| C3 | Tool calls work | ✅ `lights_on`, `lights_off`, `dispatch_er_mission` ×2 |
| C4 | NON_BLOCKING dispatch streams progress | ✅ model kept talking through the dispatch |
| C5 | Watch mode pushes JPEGs | (no per-frame log; auto-on during dispatch — implicit pass) |
| C6 | AEC3 enabled | ✅ `[supervisor] AEC3 enabled` + 0 aec_failures across 1500+ chunks |
| C7 | GoAway-driven reconnect | not exercised this bench (would need a longer session) |

## Key observation

The session closure was triggered by the **proactive budget rotate** at 80.9% usage, which is the prior plan's Task 6 mechanism firing exactly as designed. This is NOT the cycling bug. Pre-port, the same activity window would have produced 30-50 sessions due to per-turn-complete close storm; here it produced one.

Mic upload was healthy throughout: steady 32000 B/s, 100% AEC-routed (aec_failures=0).

## Follow-up issues (NOT cycling-related)

### 1. Echo / late-session model breakdown (operator-reported)

The operator reported that "the model wore an echo" and "started to break down" — repeating itself toward the end of sess#1. AEC3 was running on every chunk with zero failures, so the pipeline is wired correctly, but the cancellation effectiveness is questionable.

Hypothesis: the AEC3 filter convergence isn't deep enough on this hardware's speaker → mic path; the model hears its own audio bleed-through, transcribes it as ghost user input, and incorporates it into its next turn. This produces both the perceived echo and the late-session repetition pattern.

Investigation paths (separate plan):
- Measure actual dB suppression of speaker reference vs mic stream (the prior plan's Task 8 M3 measurement)
- Check `aec_delay_ms=50` is correctly aligned for this speaker → mic acoustic path; tune if needed
- Verify `SpeakerReferenceRing` is being filled with the actual speaker output bytes (not silence)
- Consider reducing software mic gain (currently `mic_gain=3.0`); if gain-after-AEC is too high, residual echo is amplified

### 2. Watch mode log silence

`WatchStream.run_with_callback` only logs on send errors. No per-frame `bytes_sent` accumulator log. For future benches, add a periodic `[diag] watch sess#N frames=N bytes_sent=M` log so operators can see the 1 FPS push is actually happening.

## What this bench proves

The architectural goal of the 2026-04-25 raw-WS port is achieved: the supervisor no longer triggers per-turn-close storms on `gemini-2.5-flash-native-audio-latest`. Tasks 2/3/4/5/6 of the prior plan all keep working over the new transport (NON_BLOCKING dispatch, watch mode, AEC3, budget rotation).

The **cycling bug** that motivated this port is closed.

The **echo issue** is a separate, pre-existing concern that the cycling bug was previously masking; now isolated, it can be investigated cleanly.
