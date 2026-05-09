# Web Tools Robustness — Fetch Limit Increase & Search Failure Guidance

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Raise the web_fetch character limit to handle full web pages (models have huge context windows), and make web_search failures return actionable guidance so models rephrase queries instead of retrying the same failing search in a loop.

**Architecture:** All changes are in `Orchestrator/web_tools.py` (core logic) and `Orchestrator/routes/chat_routes.py` (tool definitions that tell models about the parameters). The tool execution call sites in chat_routes.py, blackbox_tools.py, MCP server, and phone bridge do NOT need changes — they already pass through `max_chars` and return the string result as-is.

**Tech Stack:** Python, ddgs library (DuckDuckGo), requests, BeautifulSoup

---

## Task 1: Raise WebFetch Character Limit

**Why:** The current 10K char default is far too conservative. Most useful web pages produce 20K-80K of clean text after HTML stripping. Modern LLMs have 128K-200K+ token context windows. The 10K limit causes most fetches to be heavily truncated, losing the information the model actually needs. Raising to 80K maxes out coverage for virtually all web pages while staying well within model limits.

**Files:**
- Modify: `Orchestrator/web_tools.py:47` (default constant)
- Modify: `Orchestrator/routes/chat_routes.py:80-82,121-122,161-162` (tool descriptions for all 3 formats)
- Modify: `Orchestrator/tools/blackbox_tools.py` (tool descriptions for all 3 formats)
- Modify: `MCP/blackbox_mcp_server.py` (MCP tool description)

### Step 1: Update the default constant in web_tools.py

In `Orchestrator/web_tools.py`, line 47, change:

```python
MAX_CONTENT_CHARS = 10000  # Return max 10K chars
```

to:

```python
MAX_CONTENT_CHARS = 80000  # Return max 80K chars (models have 128K+ token context windows)
```

Also update `MAX_FETCH_SIZE` on line 46 from 500KB to 1MB to allow fetching larger raw HTML before parsing:

```python
MAX_FETCH_SIZE = 1000000  # 1MB raw HTML download limit
```

### Step 2: Update all tool descriptions to advertise the new default

Every tool definition tells the model what `max_chars` defaults to. Update all of them from `10000` to `80000`.

**Anthropic format** — `Orchestrator/routes/chat_routes.py`, lines 78-82:
```python
"max_chars": {
    "type": "integer",
    "description": "Maximum characters to return (default 80000, optional)",
    "default": 80000
}
```

**OpenAI format** — `Orchestrator/routes/chat_routes.py`, lines 119-123:
```python
"max_chars": {
    "type": "integer",
    "description": "Maximum characters to return (default 80000)",
    "default": 80000
}
```

**Gemini format** — `Orchestrator/routes/chat_routes.py`, lines 159-163:
```python
"max_chars": {
    "type": "integer",
    "description": "Maximum characters to return",
    "default": 80000
}
```

**blackbox_tools.py** — Find the `web_fetch` tool definitions in all 3 formats (Anthropic ~line 269, OpenAI ~line 782, Gemini ~line 1163) and update `default: 10000` → `default: 80000` and update any description text mentioning 10000.

**MCP server** — `MCP/blackbox_mcp_server.py`, find the `web_fetch` tool definition (~line 635) and update the default from 10000 to 80000.

### Step 3: Update hardcoded fallback defaults in execution call sites

In every place that does `max_chars = args.get("max_chars", 10000)`, change the fallback to 80000. These are in `chat_routes.py` at multiple locations:

- Non-streaming handlers: ~lines 1609, 1765, 2378, 2892
- Streaming handlers: ~lines 3090, 3768, 5322, 5985
- Computer use handler: ~line 4800

Also in `blackbox_tools.py` line 1850:
```python
max_chars = params.get("max_chars", 80000)
```

And in `MCP/blackbox_mcp_server.py` ~line 851:
```python
max_chars = arguments.get("max_chars", 80000)
```

### Step 4: Improve the truncation message

In `Orchestrator/web_tools.py`, lines 255-256, improve the truncation message so the model knows it can request more:

Change:
```python
if len(clean_text) > max_chars:
    clean_text = clean_text[:max_chars] + f"\n\n... (truncated, showing {max_chars} of {len(clean_text)} characters)"
```

To:
```python
if len(clean_text) > max_chars:
    clean_text = clean_text[:max_chars] + f"\n\n... (content truncated: showing {max_chars} of {len(clean_text)} total characters. To see more, call web_fetch again with a higher max_chars value.)"
```

### Step 5: Commit

```bash
git add Orchestrator/web_tools.py Orchestrator/routes/chat_routes.py Orchestrator/tools/blackbox_tools.py MCP/blackbox_mcp_server.py
git commit -m "feat(web_tools): raise web_fetch default limit from 10K to 80K chars

Models have 128K-200K+ token context windows but web_fetch was
truncating at 10K characters, losing most page content. Also
bumped raw HTML download limit from 500KB to 1MB."
```

---

## Task 2: Add Actionable Guidance to Search Failures

**Why:** When DuckDuckGo returns zero results or errors, the model gets a bare error string like `"❌ No search results found for: {query}"`. Without guidance, models retry the exact same query up to 5 times (the `max_tool_calls` limit), burning tokens and either crashing or returning nothing. The fix: return error messages that explicitly instruct the model to rephrase, broaden, or fall back — so it adapts instead of looping.

**Files:**
- Modify: `Orchestrator/web_tools.py:134-163` (search function error handling)

### Step 1: Replace the zero-results return

In `Orchestrator/web_tools.py`, line 142-143, change:

```python
if not search_results:
    return f"❌ No search results found for: {query}"
```

To:

```python
if not search_results:
    return (
        f"⚠️ No results found for: \"{query}\"\n\n"
        f"IMPORTANT: Do NOT retry with the same query. Instead:\n"
        f"1. Rephrase using different keywords or simpler terms\n"
        f"2. Break a complex query into shorter, more general searches\n"
        f"3. Remove special characters, quotes, or very specific phrases\n"
        f"4. If multiple rephrased searches still fail, inform the user that "
        f"this topic may not have web results available and answer from your own knowledge"
    )
```

### Step 2: Add specific exception handling for ddgs library errors

In `Orchestrator/web_tools.py`, replace the generic exception handler at lines 161-163. Change the entire try/except block starting at line 134 from:

```python
    try:
        print(f"[WEB_SEARCH] Searching DuckDuckGo for: {query}")

        # Use ddgs library for reliable search
        search_results = DDGS().text(query, max_results=max_results)

        print(f"[WEB_SEARCH] Query: {query} - Found {len(search_results)} results")

        if not search_results:
            # ... (already updated in Step 1)

        # ... rest of success path stays the same ...

    except Exception as e:
        print(f"[WEB_SEARCH] Error: {str(e)}")
        return f"❌ Web search error: {str(e)}"
```

To:

```python
    try:
        print(f"[WEB_SEARCH] Searching DuckDuckGo for: {query}")

        # Use ddgs library for reliable search
        search_results = DDGS().text(query, max_results=max_results)

        print(f"[WEB_SEARCH] Query: {query} - Found {len(search_results)} results")

        if not search_results:
            return (
                f"⚠️ No results found for: \"{query}\"\n\n"
                f"IMPORTANT: Do NOT retry with the same query. Instead:\n"
                f"1. Rephrase using different keywords or simpler terms\n"
                f"2. Break a complex query into shorter, more general searches\n"
                f"3. Remove special characters, quotes, or very specific phrases\n"
                f"4. If multiple rephrased searches still fail, inform the user that "
                f"this topic may not have web results available and answer from your own knowledge"
            )

        # ... rest of success path stays exactly the same ...

    except Exception as e:
        error_msg = str(e)
        error_type = type(e).__name__
        print(f"[WEB_SEARCH] Error ({error_type}): {error_msg}")

        # Provide specific guidance based on error type
        if "ratelimit" in error_type.lower() or "ratelimit" in error_msg.lower():
            return (
                f"⚠️ Search rate limited. The search service is temporarily throttling requests.\n\n"
                f"IMPORTANT: Do NOT retry immediately. Instead:\n"
                f"1. Answer the user's question from your own knowledge if possible\n"
                f"2. If web information is critical, tell the user to try again in a minute"
            )
        elif "timeout" in error_type.lower() or "timeout" in error_msg.lower():
            return (
                f"⚠️ Search timed out for: \"{query}\"\n\n"
                f"IMPORTANT: Do NOT retry the same query. Instead:\n"
                f"1. Try a shorter, simpler search query\n"
                f"2. If the search keeps timing out, answer from your own knowledge\n"
                f"3. Inform the user that web search is temporarily slow"
            )
        else:
            return (
                f"⚠️ Search failed for: \"{query}\" (error: {error_msg})\n\n"
                f"IMPORTANT: Do NOT retry the same query. Instead:\n"
                f"1. Try rephrasing with different keywords\n"
                f"2. If search is unavailable, answer from your own knowledge\n"
                f"3. Let the user know web search encountered an issue"
            )
```

### Step 3: Add the same guidance to web_fetch failures

In `Orchestrator/web_tools.py`, update the fetch error returns (lines 208-221, 279-285) to include guidance:

Change line 209:
```python
return f"❌ Failed to fetch URL (status {response.status_code}): {url}"
```
To:
```python
return (
    f"⚠️ Failed to fetch URL (HTTP {response.status_code}): {url}\n\n"
    f"IMPORTANT: Do NOT retry the same URL. Instead:\n"
    f"1. If you have search results, try a different URL from the results\n"
    f"2. If the page requires authentication, inform the user\n"
    f"3. Try searching for the same information using web_search instead"
)
```

Change line 214 (content too large from Content-Length header):
```python
return f"❌ Content too large ({int(content_length)} bytes, max {MAX_FETCH_SIZE}): {url}"
```
To:
```python
return (
    f"⚠️ Page too large to fetch ({int(content_length)} bytes): {url}\n\n"
    f"The page exceeds the download limit. Try:\n"
    f"1. Search for the specific information you need using web_search instead\n"
    f"2. Try a different, more focused page on the same topic"
)
```

Change line 221 (content too large during streaming):
```python
return f"❌ Content exceeded size limit ({MAX_FETCH_SIZE} bytes): {url}"
```
To:
```python
return (
    f"⚠️ Page too large to fetch (exceeded {MAX_FETCH_SIZE} bytes): {url}\n\n"
    f"The page exceeds the download limit. Try:\n"
    f"1. Search for the specific information you need using web_search instead\n"
    f"2. Try a different, more focused page on the same topic"
)
```

Change lines 279-282 (timeout and network errors):
```python
except requests.Timeout:
    return f"⏱️ Timeout fetching URL (>{FETCH_TIMEOUT}s): {url}"
except requests.RequestException as e:
    return f"❌ Network error fetching URL: {url}\nError: {str(e)}"
```
To:
```python
except requests.Timeout:
    return (
        f"⚠️ Timeout fetching URL (>{FETCH_TIMEOUT}s): {url}\n\n"
        f"IMPORTANT: Do NOT retry the same URL. The page is too slow to respond. Instead:\n"
        f"1. Try a different URL from your search results\n"
        f"2. Use web_search to find the information from a different source"
    )
except requests.RequestException as e:
    return (
        f"⚠️ Network error fetching URL: {url}\nError: {str(e)}\n\n"
        f"IMPORTANT: Do NOT retry the same URL. Instead:\n"
        f"1. Try a different URL from your search results\n"
        f"2. Use web_search to find alternative sources"
    )
```

Change lines 283-285 (generic parse error):
```python
except Exception as e:
    print(f"[WEB_FETCH] Error: {str(e)}")
    return f"❌ Error parsing content from {url}: {str(e)}"
```
To:
```python
except Exception as e:
    print(f"[WEB_FETCH] Error: {str(e)}")
    return (
        f"⚠️ Error parsing content from {url}: {str(e)}\n\n"
        f"IMPORTANT: Do NOT retry the same URL. Instead:\n"
        f"1. Try a different URL from your search results\n"
        f"2. The page may have unusual formatting — try a different source"
    )
```

### Step 4: Commit

```bash
git add Orchestrator/web_tools.py
git commit -m "fix(web_tools): add actionable guidance to search/fetch failures

Models were retrying identical failing searches up to 5 times
because error messages had no guidance. Now all error returns
include explicit instructions to rephrase, try alternatives,
or fall back to existing knowledge. Handles rate limits,
timeouts, zero results, and fetch failures specifically."
```

---

## Task 3: Increase Fetch Timeout for Larger Pages

**Why:** With the raised content limit (80K chars), some pages will take longer to download. The current 15-second timeout may be too tight for slower sites serving larger pages.

**Files:**
- Modify: `Orchestrator/web_tools.py:45` (timeout constant)

### Step 1: Raise the fetch timeout

In `Orchestrator/web_tools.py`, line 45, change:

```python
FETCH_TIMEOUT = 15  # seconds
```

To:

```python
FETCH_TIMEOUT = 25  # seconds (allows time for larger pages)
```

### Step 2: Commit

```bash
git add Orchestrator/web_tools.py
git commit -m "chore(web_tools): raise fetch timeout to 25s for larger pages"
```

---

## Task 4: Restart Service and Verify

### Step 1: Restart the BlackBox service

```bash
sudo systemctl restart blackbox.service
```

Wait 60-90 seconds for the service to fully start (snapshot index rebuild).

### Step 2: Verify web_fetch works with higher limit

```bash
curl -s -X POST http://localhost:9091/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Fetch this URL and tell me how many characters you got: https://en.wikipedia.org/wiki/Python_(programming_language)",
    "model": "claude",
    "operator": "Brandon-DEV"
  }' | python3 -m json.tool | head -20
```

Expected: The model successfully fetches and summarizes the Wikipedia page with significantly more content than before (should be close to 80K chars for a big page).

### Step 3: Verify search failure guidance works

```bash
# Test from the Python module directly
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc
source Orchestrator/venv/bin/activate
python3 -c "
from Orchestrator.web_tools import perform_web_search
result = perform_web_search('xyzzy_nonexistent_gibberish_12345', max_results=3)
print(result)
assert 'Do NOT retry' in result or 'Rephrase' in result or 'IMPORTANT' in result, 'Guidance missing from error message'
print('PASS: Error message includes guidance')
"
```

### Step 4: Commit snapshot

Use `/snapshot-dev` to document the changes.

---

## Summary of All Changes

| File | Change | Why |
|------|--------|-----|
| `Orchestrator/web_tools.py:45` | `FETCH_TIMEOUT` 15→25 | More time for larger pages |
| `Orchestrator/web_tools.py:46` | `MAX_FETCH_SIZE` 500K→1MB | Allow larger raw HTML downloads |
| `Orchestrator/web_tools.py:47` | `MAX_CONTENT_CHARS` 10K→80K | Models have huge context windows |
| `Orchestrator/web_tools.py:142-163` | Search error handling | Actionable guidance stops retry loops |
| `Orchestrator/web_tools.py:208-285` | Fetch error handling | Actionable guidance for all failure modes |
| `Orchestrator/web_tools.py:255-256` | Truncation message | Tell model it can request more |
| `Orchestrator/routes/chat_routes.py` | Tool defs + fallback defaults | Advertise 80K default, update all `get("max_chars", 80000)` |
| `Orchestrator/tools/blackbox_tools.py` | Tool defs + fallback default | Same as above |
| `MCP/blackbox_mcp_server.py` | Tool def + fallback default | Same as above |

**What does NOT change:**
- Tool names stay the same (`web_search`, `web_fetch`)
- Parameter names stay the same
- Return format stays the same (string)
- Phone bridge call sites don't need changes (they pass through)
- No new dependencies
