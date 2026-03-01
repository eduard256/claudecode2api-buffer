"""
Claude Code API client with SSE streaming support.

Handles communication with claudecode2api: sending chat requests
and parsing SSE event streams. All operations are async using httpx.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from app.config import config

logger = logging.getLogger("buffer")


@dataclass
class ClaudeResponse:
    """
    Accumulated response from a Claude API streaming session.

    Attributes:
        session_id: Session identifier returned by the API.
        text: Accumulated text response from the assistant.
        tool_calls: List of tool invocations with their results.
        total_cost_usd: Total cost of the request.
        is_error: Whether the final result was an error.
        started_at: Timestamp when the request started.
    """
    session_id: str | None = None
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    total_cost_usd: float = 0.0
    is_error: bool = False
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


def _auth() -> httpx.BasicAuth:
    """Build HTTP Basic Auth from config credentials."""
    return httpx.BasicAuth(config.claude_api_user, config.claude_api_pass)


async def send_chat(
    prompt: str,
    session_id: str | None,
    on_text: callable | None = None,
    on_tool_use: callable | None = None,
    on_tool_result: callable | None = None,
) -> ClaudeResponse:
    """
    Send a chat request to claudecode2api and stream the SSE response.

    Builds the request payload from config (tools, allowed_tools, system_prompt,
    workspace_dir) and streams the response, parsing each SSE event. Callbacks
    are invoked for text chunks, tool use requests, and tool results.

    Args:
        prompt: The user message to send.
        session_id: Optional session ID to continue an existing conversation.
        on_text: Callback(text: str) invoked when assistant text arrives.
        on_tool_use: Callback(tool: str, input: str) invoked when a tool is called.
        on_tool_result: Callback(tool_id: str, result: str, is_error: bool) invoked
            when a tool result is received.

    Returns:
        ClaudeResponse with accumulated results from the entire stream.

    Raises:
        httpx.HTTPStatusError: If the API returns an error HTTP status.
        Exception: Re-raised from stream parsing for unexpected errors.
    """
    response = ClaudeResponse()
    system_prompt = config.load_system_prompt()

    payload: dict = {
        "prompt": prompt,
        "cwd": config.workspace_dir,
        "tools": config.claude_tools,
        "allowed_tools": config.claude_allowed_tools,
    }
    if session_id:
        payload["session_id"] = session_id
    if system_prompt:
        payload["system_prompt"] = system_prompt

    logger.info("CLAUDE REQUEST: session=%s", session_id or "new")

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
        async with client.stream(
            "POST",
            f"{config.claude_api_url}/chat",
            json=payload,
            auth=_auth(),
            headers={"Content-Type": "application/json"},
        ) as stream:
            stream.raise_for_status()

            event_type = None
            async for line in stream.aiter_lines():
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                    continue

                if not line.startswith("data:"):
                    continue

                raw = line[5:].strip()
                if not raw:
                    continue

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("CLAUDE PARSE ERROR: %s", raw[:200])
                    continue

                _process_event(data, event_type, response, on_text, on_tool_use, on_tool_result)

                if event_type == "done":
                    break

    return response


def _process_event(
    data: dict,
    event_type: str | None,
    response: ClaudeResponse,
    on_text: callable | None,
    on_tool_use: callable | None,
    on_tool_result: callable | None,
) -> None:
    """
    Process a single parsed SSE event and update the response accumulator.

    Handles system init (extracts session_id), assistant text and tool_use
    content blocks, user tool_result messages, and final result events.

    Args:
        data: Parsed JSON data from the SSE event.
        event_type: The SSE event type ("message" or "done").
        response: The ClaudeResponse being accumulated.
        on_text: Optional callback for text content.
        on_tool_use: Optional callback for tool use events.
        on_tool_result: Optional callback for tool result events.
    """
    msg_type = data.get("type")

    if msg_type == "system" and data.get("subtype") == "init":
        response.session_id = data.get("session_id")
        return

    if msg_type == "assistant":
        message = data.get("message", {})
        for block in message.get("content", []):
            if block.get("type") == "text":
                text = block.get("text", "")
                response.text += text
                if on_text:
                    on_text(text)
                logger.info("CLAUDE TEXT: %s", text[:100])

            elif block.get("type") == "tool_use":
                tool_name = block.get("name", "")
                tool_input = block.get("input", {})
                input_str = _summarize_tool_input(tool_name, tool_input)
                response.tool_calls.append({
                    "tool": tool_name,
                    "input": input_str,
                    "result": None,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "tool_use_id": block.get("id"),
                })
                if on_tool_use:
                    on_tool_use(tool_name, input_str)
                logger.info('CLAUDE TOOL: %s "%s"', tool_name, input_str[:80])

    elif msg_type == "user":
        message = data.get("message", {})
        for block in message.get("content", []):
            if block.get("type") == "tool_result":
                tool_id = block.get("tool_use_id", "")
                content = block.get("content", "")
                is_error = block.get("is_error", False)
                # Match result to the corresponding tool call
                for tc in reversed(response.tool_calls):
                    if tc.get("tool_use_id") == tool_id:
                        tc["result"] = str(content)[:500]
                        break
                if on_tool_result:
                    on_tool_result(tool_id, str(content), is_error)

    elif msg_type == "result":
        result_text = data.get("result", "")
        response.text = result_text
        response.is_error = data.get("is_error", False)
        response.total_cost_usd = data.get("total_cost_usd", 0.0)
        response.session_id = data.get("session_id", response.session_id)
        logger.info("CLAUDE DONE: %s", result_text[:120])


def _summarize_tool_input(tool_name: str, tool_input: dict) -> str:
    """
    Create a short summary string of a tool's input for logging.

    For Bash tools, returns the command. For Read, returns the file path.
    For other tools, returns a truncated JSON representation.

    Args:
        tool_name: Name of the tool being invoked.
        tool_input: The tool's input parameters dict.

    Returns:
        A concise string representation of the input.
    """
    if tool_name == "Bash":
        return tool_input.get("command", str(tool_input))
    if tool_name == "Read":
        return tool_input.get("file_path", str(tool_input))
    return json.dumps(tool_input, ensure_ascii=False)[:150]
