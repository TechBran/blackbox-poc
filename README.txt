BlackBox Orchestrator — PoC Starter
===================================
Run locally with a minimal portal UI and working mint/assert/archive.

Quick start
-----------
1) Install:
   pip install fastapi uvicorn pydantic python-dotenv requests

2) Copy Secrets/.env.example to Secrets/.env and set OPENAI_API_KEY.

3) Launch:
   uvicorn Orchestrator.app:app --host 0.0.0.0 --port 8080

4) Open:
   http://localhost:8080/ui/index.html

5) Test:
   - Health → see GM OK
   - MINT → append a PoC snapshot
   - ASSERT → bytes + sha256
   - DOWNLOAD → latest archived volume

Notes
-----
- GoldMaster/v7.0.txt is a sample; hash is verified at runtime.
- The PoC tail/recall logic is minimal; expand for full triple-pass unanimity and stricter parsing.
- All writes are atomic (.part → replace).

Next
----
- Add policy counters (turns/tokens/DriftLight) and auto-mint.
- Add retrieval (keyword/BM25) and JSON-schema LLM enrichment with fallback.
- Add QR pairing and (later) n3n/Tailscale for remote access.
