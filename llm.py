"""Claude access for the agents.

With ANTHROPIC_API_KEY set, agents call Claude. Without it, the caller's mock
function supplies deterministic sample output so the whole app can be tried
for free.
"""
import json
import os
import re


class LLMError(Exception):
    """Raised when an agent cannot produce usable output."""


def _extract_json(text):
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    body = fenced.group(1) if fenced else text
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("The agent returned an unreadable answer. Try again.")
    try:
        return json.loads(body[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError("The agent returned broken JSON. Try again.") from exc


def generate_json(system, prompt, mock):
    """Return (data, usage). `mock` is a zero-argument callable used without an API key."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return mock(), {"input": 0, "output": 0, "mock": True}
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        message = client.messages.create(
            model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5"),
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
    except LLMError:
        raise
    except Exception as exc:  # network, auth, rate limit
        raise LLMError(f"Claude request failed: {exc}") from exc
    text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
    data = _extract_json(text)
    usage = {
        "input": getattr(message.usage, "input_tokens", 0),
        "output": getattr(message.usage, "output_tokens", 0),
        "mock": False,
    }
    return data, usage
