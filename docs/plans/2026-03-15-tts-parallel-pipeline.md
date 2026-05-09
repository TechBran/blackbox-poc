# TTS Parallel Generation Pipeline

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make TTS audio generation dramatically faster by generating all text chunks concurrently and stitching audio properly at the PCM level.

**Architecture:** New backend `/tts/batch` endpoint handles text chunking, parallel API calls (via asyncio.gather + ThreadPoolExecutor), and proper WAV/MP3 stitching. Frontend simplified to a single batch request for the speak-button flow. StreamingTTSQueue upgraded to fire concurrent TTS requests instead of sequential. All three providers (OpenAI, Gemini Pro, Gemini Flash) benefit.

**Tech Stack:** Python asyncio + ThreadPoolExecutor (backend parallelism), Python `wave` module (WAV stitching), existing OpenAI/Gemini TTS APIs, JavaScript Promise.all (streaming parallelism)

---

## Current Problems

1. **Sequential chunk generation** — `speakToBubble()` at `tts-stt.js:1011` uses `for...await` loop. Each chunk waits for the previous to finish. A 3-chunk message takes 3x the time of one chunk.
2. **Gemini TTS task queue overhead** — Each chunk goes through task creation → worker polling (2s sleep) → frontend status polling (1-3s intervals). Adds 3-5 seconds of pure overhead per chunk.
3. **Chunk size too large** — 4000 chars per chunk means fewer chunks but each takes 5-15 seconds. Smaller chunks (600-800 chars) generate in 1-3 seconds and parallelize better.
4. **Broken WAV concatenation** — `new Blob(audioBlobs)` at lines 405, 1045, 1454 naively concatenates WAV files including headers. The second WAV header gets interpreted as audio data → static/click artifacts. (MP3 concatenation works fine since MP3 is frame-based.)
5. **StreamingTTSQueue processes sequentially** — `_processQueue()` at line 224 has `if (this.isGenerating) return` gate. Only one sentence generates at a time.

## Solution Overview

```
BEFORE (sequential, 3 chunks):
  Chunk 1: [====3s====] → Chunk 2: [====3s====] → Chunk 3: [====3s====] → Stitch → Play
  Total: ~9 seconds

AFTER (parallel, 3 chunks):
  Chunk 1: [====3s====]
  Chunk 2: [====3s====]  → Stitch → Play
  Chunk 3: [====3s====]
  Total: ~3 seconds (+ stitch overhead <100ms)
```

---

### Task 1: Backend — Text chunking utility

**Files:**
- Modify: `Orchestrator/routes/tts_routes.py` (add function after `pcm_to_wav` at ~line 784)

**Step 1: Add chunk_text_for_tts function**

Add this function to `tts_routes.py` after the `pcm_to_wav` function (around line 784):

```python
def chunk_text_for_tts(text: str, max_chars: int = 800) -> list[str]:
    """Split text at sentence boundaries for TTS chunking.

    Args:
        text: Full text to split
        max_chars: Maximum characters per chunk (default 800 for fast generation)

    Returns:
        List of text chunks, each under max_chars
    """
    if not text or not text.strip():
        return []

    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    import re
    chunks = []
    # Split at sentence boundaries: period/exclamation/question followed by whitespace, or double newlines
    sentences = re.split(r'(?<=[.!?])\s+|(?<=\n)\n', text)

    buf = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        candidate = f"{buf} {sentence}".strip() if buf else sentence

        if len(candidate) <= max_chars:
            buf = candidate
        else:
            if buf:
                chunks.append(buf)
            # Handle single sentence exceeding max_chars
            if len(sentence) > max_chars:
                words = sentence.split()
                word_buf = ""
                for word in words:
                    candidate_word = f"{word_buf} {word}".strip() if word_buf else word
                    if len(candidate_word) <= max_chars:
                        word_buf = candidate_word
                    else:
                        if word_buf:
                            chunks.append(word_buf)
                        word_buf = word
                buf = word_buf
            else:
                buf = sentence

    if buf and buf.strip():
        chunks.append(buf.strip())

    print(f"[TTS Batch] Split {len(text)} chars into {len(chunks)} chunks: {[len(c) for c in chunks]}")
    return chunks
```

**Step 2: Verify**

Run: `python3 -c "import sys; sys.path.insert(0,'.'); from Orchestrator.routes.tts_routes import chunk_text_for_tts; print(chunk_text_for_tts('Hello world. This is a test. Another sentence here.', 30))"`

Expected: List of chunks each ≤30 chars, split at sentence boundaries.

---

### Task 2: Backend — WAV stitching utility

**Files:**
- Modify: `Orchestrator/routes/tts_routes.py` (add function after `chunk_text_for_tts`)

**Step 1: Add stitch_wav_chunks function**

```python
def stitch_wav_chunks(wav_chunks: list[bytes]) -> bytes:
    """Combine multiple WAV files into one by concatenating PCM data.

    Properly strips WAV headers from subsequent chunks to avoid
    audio artifacts (clicks/static) that occur with naive blob concatenation.

    Args:
        wav_chunks: List of WAV file bytes

    Returns:
        Single combined WAV file bytes
    """
    if not wav_chunks:
        return b""
    if len(wav_chunks) == 1:
        return wav_chunks[0]

    # Read first chunk to get audio parameters
    first_buf = io.BytesIO(wav_chunks[0])
    with wave.open(first_buf, 'rb') as wf:
        params = wf.getparams()
        all_frames = wf.readframes(wf.getnframes())

    # Read remaining chunks (strip headers, keep PCM data only)
    for chunk_bytes in wav_chunks[1:]:
        chunk_buf = io.BytesIO(chunk_bytes)
        with wave.open(chunk_buf, 'rb') as wf:
            all_frames += wf.readframes(wf.getnframes())

    # Write combined WAV
    output = io.BytesIO()
    with wave.open(output, 'wb') as wf:
        wf.setparams(params)
        wf.writeframes(all_frames)

    combined = output.getvalue()
    print(f"[TTS Stitch] Combined {len(wav_chunks)} WAV chunks: {sum(len(c) for c in wav_chunks)} bytes -> {len(combined)} bytes")
    return combined
```

**Step 2: Verify import**

The `wave` and `io` modules are already imported in tts_routes.py. Verify:

Run: `grep -n "^import wave\|^import io" Orchestrator/routes/tts_routes.py`

Expected: Both imports present (they're used by `pcm_to_wav`).

---

### Task 3: Backend — POST /tts/batch endpoint

**Files:**
- Modify: `Orchestrator/routes/tts_routes.py` (add endpoint after the existing `/tts` endpoint, around line 161)

**Step 1: Add the batch TTS endpoint**

Add this after the existing `tts_openai` endpoint (around line 161, before `pair_tokens`):

```python
@app.post("/tts/batch")
async def tts_batch(body: dict = Body(...)):
    """Generate TTS for full text with automatic chunking and parallel generation.

    Splits text into optimal chunks, generates all chunks concurrently,
    and stitches audio properly at the PCM level (no artifacts).

    Supports: OpenAI (tts-1, tts-1-hd), Gemini Pro TTS, Gemini Flash TTS
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "No text provided")

    provider = (body.get("provider") or "openai").strip()
    voice = (body.get("voice") or "onyx").strip()
    model = (body.get("model") or TTS_MODEL).strip()
    audio_format = (body.get("format") or TTS_FORMAT).strip()
    operator = (body.get("operator") or "").strip()

    # Provider-specific chunk sizes: OpenAI is fast per-request, Gemini benefits from smaller chunks
    if provider == "openai":
        max_chunk = int(body.get("max_chunk_chars", 1500))
    else:
        max_chunk = int(body.get("max_chunk_chars", 800))

    # Split text into chunks
    chunks = chunk_text_for_tts(text, max_chunk)
    if not chunks:
        raise HTTPException(400, "No text to generate")

    print(f"[TTS Batch] Provider={provider}, voice={voice}, model={model}, chunks={len(chunks)}")

    # Define per-chunk generation functions
    def _generate_openai_chunk(chunk_text: str) -> bytes:
        """Generate one OpenAI TTS chunk (blocking)."""
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("OPENAI_API_KEY not configured")

        req = {
            "model": model,
            "input": chunk_text,
            "voice": voice,
            "response_format": audio_format
        }
        r = requests.post(
            OPENAI_TTS_URL,
            json=req,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=TTS_TIMEOUT / 1000.0
        )
        if r.status_code != 200:
            raise ValueError(f"OpenAI TTS API error: {r.status_code} {r.text[:200]}")
        return r.content

    def _generate_gemini_chunk(chunk_text: str) -> bytes:
        """Generate one Gemini TTS chunk (blocking). Returns WAV bytes."""
        inp = GeminiProTTSIn(
            text=chunk_text,
            voice_name=voice,
            model=model,
            operator=operator
        )
        return call_gemini_tts(inp)  # Returns WAV bytes directly

    # Generate ALL chunks in parallel
    loop = asyncio.get_event_loop()
    max_workers = min(len(chunks), 8)  # Cap at 8 concurrent to respect rate limits

    start_time = time.time()

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tts-batch") as pool:
        if provider == "openai":
            futures = [loop.run_in_executor(pool, _generate_openai_chunk, chunk) for chunk in chunks]
        else:
            futures = [loop.run_in_executor(pool, _generate_gemini_chunk, chunk) for chunk in chunks]

        try:
            results = await asyncio.gather(*futures)
        except Exception as e:
            print(f"[TTS Batch] Parallel generation failed: {e}")
            raise HTTPException(500, f"TTS generation failed: {str(e)}")

    elapsed = time.time() - start_time
    print(f"[TTS Batch] Generated {len(results)} chunks in {elapsed:.2f}s (parallel)")

    # Stitch audio
    if len(results) == 1:
        combined = results[0]
    elif provider == "openai" and audio_format == "mp3":
        # MP3 is frame-based — simple concatenation works
        combined = b"".join(results)
    else:
        # WAV needs proper header handling
        combined = stitch_wav_chunks(results)

    # Determine MIME type
    if provider == "openai":
        mime = "audio/mpeg" if audio_format == "mp3" else ("audio/ogg" if audio_format == "opus" else "audio/wav")
    else:
        mime = "audio/wav"

    total_time = time.time() - start_time
    print(f"[TTS Batch] Total: {total_time:.2f}s, size: {len(combined)} bytes, mime: {mime}")

    return StreamingResponse(iter([combined]), media_type=mime)
```

**Step 2: Verify the endpoint starts**

Run: `curl -s http://localhost:9091/docs | grep -o 'tts/batch'`

Expected: `tts/batch` appears in the OpenAPI docs.

**Step 3: Test with a short text**

Run: `curl -s -X POST http://localhost:9091/tts/batch -H "Content-Type: application/json" -d '{"text":"Hello world. This is a test.","provider":"openai","voice":"onyx"}' -o /tmp/test_batch.mp3 -w "%{http_code} %{size_download}"`

Expected: `200` with audio bytes > 1000.

**Step 4: Test with a long text (multi-chunk)**

Run: `curl -s -X POST http://localhost:9091/tts/batch -H "Content-Type: application/json" -d '{"text":"The quick brown fox jumps over the lazy dog. This sentence is just filler to make the text longer. We need enough text to trigger chunking behavior. Each chunk should be generated in parallel for maximum speed. The final audio should be seamlessly stitched together without any clicks or pops at the boundaries. This is the power of proper PCM-level audio concatenation. No more naive blob merging that creates artifacts.","provider":"openai","voice":"onyx","max_chunk_chars":100}' -o /tmp/test_batch_multi.mp3 -w "%{http_code} %{size_download}"`

Expected: `200` with audio bytes, server logs show multiple chunks generated in parallel.

**Step 5: Commit**

```bash
git add Orchestrator/routes/tts_routes.py
git commit -m "feat: add /tts/batch endpoint with parallel generation and WAV stitching"
```

---

### Task 4: Frontend — Update speakToBubble() to use batch endpoint

**Files:**
- Modify: `Portal/modules/tts-stt.js:975-1073` (rewrite `speakToBubble`)

**Step 1: Rewrite speakToBubble to use batch endpoint**

Replace the `speakToBubble` function (lines 975-1073) with:

```javascript
export async function speakToBubble(text, bubbleElement, btn) {
    if (ttsState.isFetching) {
        toast("Audio generation in progress...");
        return;
    }

    try {
        ttsState.isFetching = true;
        setBubbleState(btn, true);

        const speakableText = extractSpeakableText(text);

        if (!speakableText) {
            toast("No text to speak");
            ttsState.isFetching = false;
            setBubbleState(btn, false);
            return;
        }

        console.log(`[TTS] Starting batch generation for ${speakableText.length} characters`);
        toast("Generating audio...");

        const voiceConfig = getTTSVoice();
        const isGemini = voiceConfig.provider !== "openai";
        const audioFormat = isGemini ? "wav" : TTS_FMT;
        const operator = (localStorage.getItem("bbx_operator") || "").trim();

        // Single batch request — backend handles chunking, parallel generation, and stitching
        const r = await fetch("/tts/batch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                text: speakableText,
                provider: voiceConfig.provider,
                voice: voiceConfig.voice,
                model: isGemini ? getGeminiModel(voiceConfig.provider) : TTS_MODEL,
                format: TTS_FMT,
                operator: operator
            })
        });

        if (!r.ok) {
            const errorText = await r.text();
            console.error(`[TTS] Batch generation failed:`, errorText);
            throw new Error(`TTS generation failed: ${r.status}`);
        }

        const blob = await r.blob();
        console.log(`[TTS] Batch complete, blob size: ${blob.size} bytes`);

        const audioDataURL = await blobToDataURL(blob);

        // Cache audio
        const audioCache = getAudioCache();
        const contentKey = simpleHash(speakableText + ':' + voiceConfig.provider + ':' + voiceConfig.voice);
        console.log('[TTS] Caching audio with key:', contentKey);
        audioCache[contentKey] = audioDataURL;
        saveAudioCache();

        bubbleElement.dataset.audioKey = contentKey;
        attachAudioPlayer(bubbleElement, audioDataURL, true, btn);
        toast("Audio ready");

    } catch (e) {
        console.error("[TTS] Speak to bubble error:", e);
        toast("Audio generation failed: " + e.message);
    } finally {
        ttsState.isFetching = false;
        setBubbleState(btn, false);
    }
}
```

**Key changes:**
- Replaced sequential `for...await` chunk loop with single `/tts/batch` request
- Backend handles chunking + parallel generation + WAV stitching
- Cache key now includes voice provider and voice name (fixes voice-awareness bug)
- Simpler, fewer lines, much faster

**Step 2: Update cache lookup to match new key format**

Search for where audio cache is checked before calling `speakToBubble`. Find the cache lookup code that uses `simpleHash(speakableText)` and update it to include voice info:

In the same file, find the speak button click handler (or wherever cached audio is loaded). Look for `simpleHash(speakableText)` or `simpleHash(text)` pattern for cache reads and update to match:

```javascript
const contentKey = simpleHash(speakableText + ':' + voiceConfig.provider + ':' + voiceConfig.voice);
```

Search pattern: `simpleHash(` in tts-stt.js — update ALL cache key computations to include voice.

**Step 3: Verify the speak button works**

1. Open Portal in browser
2. Send a chat message
3. Click the speaker icon
4. Observe: Single "Generating audio..." toast, then audio plays
5. Check console: `[TTS] Batch complete` log with blob size

**Step 4: Commit**

```bash
git add Portal/modules/tts-stt.js
git commit -m "feat: speakToBubble uses /tts/batch for parallel chunk generation"
```

---

### Task 5: Frontend — Upgrade StreamingTTSQueue for concurrent generation

**Files:**
- Modify: `Portal/modules/tts-stt.js:224-254` (rewrite `_processQueue`)

**Step 1: Replace sequential _processQueue with concurrent version**

Replace the `_processQueue` method (lines 224-254) with:

```javascript
    /**
     * Process the generation queue — fires all pending sentences concurrently
     */
    async _processQueue() {
        if (this.generationQueue.length === 0) return;

        // Grab all queued sentences at once
        const batch = [...this.generationQueue];
        this.generationQueue.length = 0;

        this._updateIndicator(`Generating ${batch.length} audio segment${batch.length > 1 ? 's' : ''}...`);

        // Fire all TTS requests concurrently
        const promises = batch.map(async (sentence, i) => {
            const index = this.totalGenerated + i;
            try {
                console.log(`[StreamingTTS] Generating segment ${index + 1} concurrently`);
                const blob = await this._generateTTS(sentence);
                return { index, blob };
            } catch (err) {
                console.error(`[StreamingTTS] Segment ${index + 1} failed:`, err);
                return { index, blob: null };
            }
        });

        // Wait for all to complete
        const results = await Promise.all(promises);

        // Add blobs in order
        for (const { index, blob } of results) {
            if (blob) {
                this.audioBlobs.push(blob);
                const url = URL.createObjectURL(blob);
                this.audioUrls.push(url);
            }
        }
        this.totalGenerated += batch.length;

        console.log(`[StreamingTTS] Batch of ${batch.length} complete, total: ${this.totalGenerated}`);

        // Start playback if not already playing
        if (!this.isPlaying && this.audioUrls.length > 0) {
            this._startPlayback();
        }

        // Process any new sentences that arrived while we were generating
        if (this.generationQueue.length > 0) {
            this._processQueue();
        }
    }
```

**Step 2: Remove the isGenerating flag**

The `isGenerating` flag at line 75 (`this.isGenerating = false;`) is no longer needed as a sequential gate. However, we should keep it to prevent re-entrant calls. Update the method to use it as a re-entrancy guard:

Add at the top of the new `_processQueue`:
```javascript
    async _processQueue() {
        if (this.isGenerating || this.generationQueue.length === 0) return;
        this.isGenerating = true;

        try {
            // ... (batch generation code from above)
        } finally {
            this.isGenerating = false;
            // Process any new sentences that arrived
            if (this.generationQueue.length > 0) {
                this._processQueue();
            }
        }
    }
```

The key difference: before, `isGenerating` blocked new sentences from generating until the current one finished. Now, it batches all pending sentences and generates them concurrently. New sentences that arrive during generation are picked up in the next batch.

**Step 3: Fix _createFinalPlayer WAV stitching**

In `_createFinalPlayer` (line 389), replace the naive blob concatenation:

```javascript
    async _createFinalPlayer() {
        console.log('[StreamingTTS] Creating final player with', this.audioBlobs.length, 'chunks');

        if (this.indicator) {
            this.indicator.remove();
        }

        if (this.audioBlobs.length === 0) {
            console.log('[StreamingTTS] No audio to combine');
            return;
        }

        const isGemini = this.voiceConfig.provider !== "openai";
        const audioFormat = isGemini ? "wav" : TTS_FMT;

        let audioDataURL;

        if (this.audioBlobs.length === 1) {
            // Single chunk — no stitching needed
            audioDataURL = await blobToDataURL(this.audioBlobs[0]);
        } else if (!isGemini) {
            // MP3 — frame-based, naive concatenation works
            const combinedBlob = new Blob(this.audioBlobs, { type: `audio/${audioFormat}` });
            audioDataURL = await blobToDataURL(combinedBlob);
        } else {
            // WAV — use backend stitch endpoint to properly combine
            const formData = new FormData();
            this.audioBlobs.forEach((blob, i) => {
                formData.append('chunks', blob, `chunk_${i}.wav`);
            });

            try {
                const r = await fetch('/tts/stitch', { method: 'POST', body: formData });
                if (r.ok) {
                    const stitchedBlob = await r.blob();
                    audioDataURL = await blobToDataURL(stitchedBlob);
                } else {
                    // Fallback to naive concat if stitch endpoint fails
                    console.warn('[StreamingTTS] Stitch failed, using naive concat');
                    const combinedBlob = new Blob(this.audioBlobs, { type: `audio/${audioFormat}` });
                    audioDataURL = await blobToDataURL(combinedBlob);
                }
            } catch (e) {
                console.warn('[StreamingTTS] Stitch error, fallback:', e);
                const combinedBlob = new Blob(this.audioBlobs, { type: `audio/${audioFormat}` });
                audioDataURL = await blobToDataURL(combinedBlob);
            }
        }

        console.log('[StreamingTTS] Combined audio size:', (audioDataURL.length / 1024).toFixed(2), 'KB');

        const speakBtn = this.bubble.querySelector('.speak-btn');
        attachAudioPlayer(this.bubble, audioDataURL, false, speakBtn);

        this.audioUrls.forEach(url => URL.revokeObjectURL(url));
        toast('Streaming TTS complete');
    }
```

**Step 4: Commit**

```bash
git add Portal/modules/tts-stt.js
git commit -m "feat: StreamingTTSQueue generates sentences concurrently instead of sequentially"
```

---

### Task 6: Backend — WAV stitch endpoint for streaming case

**Files:**
- Modify: `Orchestrator/routes/tts_routes.py` (add endpoint near `/tts/batch`)

**Step 1: Add the stitch endpoint**

```python
@app.post("/tts/stitch")
async def tts_stitch(chunks: list[UploadFile] = File(...)):
    """Stitch multiple WAV audio chunks into one properly combined file.

    Accepts multipart form data with multiple WAV file chunks.
    Strips duplicate WAV headers and concatenates PCM data.
    """
    if not chunks:
        raise HTTPException(400, "No audio chunks provided")

    wav_data = []
    for chunk in chunks:
        data = await chunk.read()
        if data:
            wav_data.append(data)

    if not wav_data:
        raise HTTPException(400, "All chunks were empty")

    if len(wav_data) == 1:
        return StreamingResponse(iter([wav_data[0]]), media_type="audio/wav")

    combined = stitch_wav_chunks(wav_data)
    return StreamingResponse(iter([combined]), media_type="audio/wav")
```

**Step 2: Add UploadFile and File imports if missing**

Check if `UploadFile` and `File` are already imported from fastapi:

```python
from fastapi import UploadFile, File
```

These may already be imported. If not, add to the existing import block at the top of tts_routes.py.

**Step 3: Verify**

Run: The endpoint will be tested through the streaming TTS flow in the Portal.

**Step 4: Commit**

```bash
git add Orchestrator/routes/tts_routes.py
git commit -m "feat: add /tts/stitch endpoint for proper WAV combination"
```

---

### Task 7: Fix all remaining naive WAV concatenation in tts-stt.js

**Files:**
- Modify: `Portal/modules/tts-stt.js`

**Step 1: Find all naive Blob concatenation**

Search for `new Blob(audioBlobs` and `new Blob(this.audioBlobs` in the file. There are 3 occurrences:

1. Line 405 — `_createFinalPlayer` (already fixed in Task 5)
2. Line 1045 — `speakToBubble` (already replaced in Task 4 — no longer concatenates)
3. Line 1454 — unknown third usage

**Step 2: Check and fix line 1454**

Read the context around line 1454 to understand what it does. If it's another audio combination path, apply the same fix pattern: use `/tts/stitch` for WAV, naive concat for MP3.

**Step 3: Commit**

```bash
git add Portal/modules/tts-stt.js
git commit -m "fix: eliminate all naive WAV blob concatenation, use proper PCM stitching"
```

---

### Task 8: Integration testing and tuning

**Step 1: Test OpenAI TTS batch generation**

1. Open Portal, set voice to OpenAI (e.g., "onyx")
2. Send a long message (ask for a story or explanation)
3. Click speaker icon
4. Observe server logs: should see `[TTS Batch]` with parallel chunk generation
5. Verify: audio plays cleanly, no gaps or artifacts

**Step 2: Test Gemini TTS batch generation**

1. Switch voice to Gemini Pro (e.g., "Charon")
2. Click speaker on the same (or new) message
3. Observe server logs: `[TTS Batch]` with Gemini parallel generation
4. Verify: WAV audio plays cleanly, no clicks at chunk boundaries

**Step 3: Test streaming Auto-TTS**

1. Enable Auto-TTS toggle
2. Send a message that triggers a long response
3. Observe: Streaming indicator shows concurrent generation
4. Verify: Audio segments play sequentially as they complete
5. Verify: Final combined player has clean audio

**Step 4: Test short messages (single chunk)**

1. Send a short message ("Hello")
2. Click speaker
3. Verify: Works normally (single chunk, no unnecessary batch overhead)

**Step 5: Performance comparison**

Time the speak button for a ~2000 character message:
- Before: likely 8-15 seconds (sequential)
- After: should be 2-5 seconds (parallel)

Log the timing from server output: `[TTS Batch] Total: X.XXs`

**Step 6: Commit any tuning adjustments**

```bash
git commit -m "chore: tune TTS batch chunk sizes based on testing"
```

---

## File Change Summary

| File | Changes |
|------|---------|
| `Orchestrator/routes/tts_routes.py` | Add `chunk_text_for_tts()`, `stitch_wav_chunks()`, `POST /tts/batch`, `POST /tts/stitch` |
| `Portal/modules/tts-stt.js` | Rewrite `speakToBubble()` to use batch, upgrade `StreamingTTSQueue._processQueue()` for concurrency, fix WAV stitching in `_createFinalPlayer()`, fix cache keys to include voice |

## Risk Mitigation

- **Rate limits**: Max 8 concurrent workers per batch request. Gemini and OpenAI both handle this well.
- **Fallback**: If `/tts/batch` fails, the old sequential path can be restored by reverting the frontend change. Backend changes are additive (new endpoints).
- **WAV stitch errors**: `_createFinalPlayer` falls back to naive concat if `/tts/stitch` fails.
- **Cache invalidation**: New cache keys include voice info. Old cached audio with old keys will simply be cache misses (regenerated on next play). No data loss.
