"""
OpenAI CUA Agent Loop — placeholder for future implementation.

Uses the Responses API with computer_use_preview tool type.

Key differences from Anthropic/Gemini:
- Response items include 'reasoning' that must be passed back
- Uses previous_response_id for session continuity
- Actions: click, type, scroll, keypress, wait, screenshot
- Supported environments: browser, mac, windows, ubuntu

API format:
    response = client.responses.create(
        model="computer-use-preview",
        tools=[{
            "type": "computer_use_preview",
            "display_width": 1024,
            "display_height": 768,
            "environment": "browser"
        }],
        input=[...],
        reasoning={"summary": "concise"},
        truncation="auto"
    )
"""


async def run_openai_cu_loop(session, prompt, model=None, system_prompt=None, url=None):
    """Placeholder — to be implemented when OpenAI CUA is prioritized."""
    yield {
        "type": "error",
        "data": {
            "message": "OpenAI CUA not yet implemented. Use Anthropic (computer-use provider) "
                       "or Gemini (/gemini-cu/run endpoint) for computer use tasks."
        }
    }
