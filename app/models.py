"""
Data models for claudecode2api-buffer.

Defines Pydantic models for API requests/responses and internal
data structures for batch history and tool call tracking.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class BufferState(str, enum.Enum):
    """
    State machine states for the message buffer.

    IDLE: No messages, no timer, claude not working.
    BUFFERING: Messages in buffer, timer ticking.
    PROCESSING: Claude is working, new messages go to pending.
    """
    IDLE = "IDLE"
    BUFFERING = "BUFFERING"
    PROCESSING = "PROCESSING"


class MessageRequest(BaseModel):
    """Incoming message to be buffered. Requires non-empty text."""
    text: str = Field(..., min_length=1, description="Message text to buffer")


class ToolCall(BaseModel):
    """Record of a single tool invocation during Claude processing."""
    tool: str
    input: str
    result: str | None = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class BatchRecord(BaseModel):
    """
    Historical record of a sent message batch.

    Tracks the messages sent, timing, Claude's response,
    and whether the request was cancelled.
    """
    id: int
    messages: list[str]
    sent_at: str
    completed_at: str | None = None
    response: str | None = None
    cancelled: bool = False


class StatusResponse(BaseModel):
    """Current buffer status returned by GET /v1/status."""
    state: str
    current_buffer: list[str]
    pending_buffer: list[str]
    timer_remaining: float | None
    pending_timer_remaining: float | None
    session_id: str | None


class HistoryResponse(BaseModel):
    """Batch history returned by GET /v1/history."""
    batches: list[BatchRecord]


class ResponseStatus(BaseModel):
    """Current Claude response status returned by GET /v1/response."""
    active: bool
    text: str | None = None
    tool_calls: list[ToolCall] = []
    started_at: str | None = None
