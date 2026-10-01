"""AgentCore Runtime entrypoint for the product search/discovery eval agent.

For local testing:
    python -m app.entrypoint
    curl -N -X POST http://localhost:8080/invocations \
      -H "Content-Type: application/json" -H "Accept: text/event-stream" \
      -d '{"prompt": "show me casual mens jackets"}'

Design notes (see the eval-framework README for the full rationale):

- Uses `agent.invoke_async()` (a single blocking call that runs the full
  tool-use loop) rather than hand-parsing Strands' internal streaming event
  shapes. This runtime's only consumer is scripts/eval/run_eval.py, which
  reassembles a whole-turn result from however many SSE events arrive -- it
  doesn't need true token-level incrementality, so trading streaming
  granularity for a stable, documented API (AgentResult, agent.messages) is a
  good tradeoff here.
- Conversation history lives in an in-memory dict keyed by session_id, scoped
  to this process. AgentCore pins a warm microVM per session_id, so a
  multi-turn eval run (same session_id across turns) keeps working as long as
  the microVM stays warm; a cold restart loses history, same as any
  in-memory-only service. There's no S3/DynamoDB session store here -- a
  cross-restart session store is out of scope for a search/discovery-only
  eval runtime.
- config_overrides.agent_model_id lets scripts/eval/run_eval.py's
  --agent-model/--compare-models switch models per request, gated by
  ALLOW_CONFIG_OVERRIDES (same mechanism/env-var name as the source repo).
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent

from app.catalog_search import get_cached_product, search_catalog
from app.prompt import SYSTEM_PROMPT

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("eval_runtime")

app = BedrockAgentCoreApp()

DEFAULT_MODEL_ID = os.environ.get("AGENT_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
ALLOW_CONFIG_OVERRIDES = os.environ.get("ALLOW_CONFIG_OVERRIDES", "false").lower() == "true"
MAX_PROMPT_LENGTH = int(os.environ.get("MAX_PROMPT_LENGTH", "4000"))

# In-memory, per-process conversation history. See module docstring.
_sessions: dict[str, list[dict]] = {}

_PRODUCT_TAG_RE = re.compile(r'<product\s+sku="([^"]+)"\s*/?>')
_SUGGESTED_REPLIES_RE = re.compile(r"<suggested_replies>(.*?)</suggested_replies>", re.DOTALL)


def _extract_content_blocks(text: str) -> list[dict]:
    """Split final assistant text into text/product/suggested_replies blocks.

    The model is instructed (see prompt.py) to reference shown products with
    <product sku="..."/> tags and to offer quick replies with a trailing
    <suggested_replies>[...]</suggested_replies> line. This turns those tags
    into the content-block shapes scripts/eval/run_eval.py already knows how
    to parse ({"product": {...}}, {"suggested_replies": [...]}),
    """
    blocks: list[dict] = []

    suggested_replies: list[str] | None = None
    sr_match = _SUGGESTED_REPLIES_RE.search(text)
    if sr_match:
        try:
            parsed = json.loads(sr_match.group(1))
            if isinstance(parsed, list):
                suggested_replies = [str(x) for x in parsed]
        except json.JSONDecodeError:
            logger.warning("Could not parse suggested_replies block: %r", sr_match.group(1))
        text = text[: sr_match.start()] + text[sr_match.end() :]

    last_end = 0
    for tag_match in _PRODUCT_TAG_RE.finditer(text):
        if tag_match.start() > last_end:
            segment = text[last_end : tag_match.start()]
            if segment.strip():
                blocks.append({"text": segment})
        product = get_cached_product(tag_match.group(1))
        if product:
            blocks.append({"product": product})
        else:
            logger.warning("Model referenced unknown/stale sku=%s", tag_match.group(1))
        last_end = tag_match.end()
    if last_end < len(text):
        segment = text[last_end:]
        if segment.strip():
            blocks.append({"text": segment})

    if suggested_replies:
        blocks.append({"suggested_replies": suggested_replies})

    return blocks


def _tool_blocks(message: dict) -> list[dict]:
    out = []
    for block in message.get("content", []):
        if "toolUse" in block:
            out.append({"toolUse": block["toolUse"]})
        elif "toolResult" in block:
            out.append({"toolResult": block["toolResult"]})
    return out


@app.entrypoint
async def invoke(payload: dict):
    prompt = (payload.get("prompt") or "")[:MAX_PROMPT_LENGTH]
    session_id = payload.get("session_id") or str(uuid.uuid4())
    config_overrides = payload.get("config_overrides") or {}

    model_id = DEFAULT_MODEL_ID
    if ALLOW_CONFIG_OVERRIDES and config_overrides.get("agent_model_id"):
        model_id = config_overrides["agent_model_id"]

    history = _sessions.get(session_id, [])
    agent = Agent(model=model_id, system_prompt=SYSTEM_PROMPT, tools=[search_catalog], messages=history)
    start_idx = len(agent.messages)

    try:
        result = await agent.invoke_async(prompt)
    except Exception as e:
        logger.exception("agent invocation failed")
        yield {
            "session_id": session_id,
            "message": {"content": [{"text": f"Sorry, something went wrong processing that request: {e}"}]},
        }
        return

    _sessions[session_id] = agent.messages

    # Forward tool-use/tool-result blocks from any intermediate turns (not
    # the final assistant message, handled separately below) -- useful for
    # the eval harness's tool-call reporting, not required for scoring.
    for message in agent.messages[start_idx:-1]:
        blocks = _tool_blocks(message)
        if blocks:
            yield {"session_id": session_id, "message": {"content": blocks}}

    final_blocks: list[dict] = []
    for block in result.message.get("content", []):
        if "text" in block:
            final_blocks.extend(_extract_content_blocks(block["text"]))
        elif "toolUse" in block:
            final_blocks.append({"toolUse": block["toolUse"]})
    if final_blocks:
        yield {"session_id": session_id, "message": {"content": final_blocks}}

    usage = result.metrics.accumulated_usage or {}
    yield {
        "session_id": session_id,
        "metrics": {
            "cycleCount": result.metrics.cycle_count,
            "inputTokens": usage.get("inputTokens", 0),
            "outputTokens": usage.get("outputTokens", 0),
            "cacheReadInputTokens": usage.get("cacheReadInputTokens", 0),
            "cacheWriteInputTokens": usage.get("cacheWriteInputTokens", 0),
        },
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0")
