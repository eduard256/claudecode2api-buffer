"""
FastAPI endpoints for claudecode2api-buffer.

Provides the HTTP API layer: health check, message ingestion,
session management, buffer status, batch history, and current
response polling. All endpoints except /v1/health require Basic Auth.
"""

from __future__ import annotations

import secrets
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import config
from app.models import (
    HistoryResponse,
    MessageRequest,
    ResponseStatus,
    StatusResponse,
    ToolCall,
)
from app.buffer import buffer

router = APIRouter()
security = HTTPBasic()


def verify_auth(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    """
    Verify HTTP Basic Auth credentials against configured values.

    Uses constant-time comparison to prevent timing attacks.

    Args:
        credentials: The parsed Basic Auth credentials from the request.

    Raises:
        HTTPException 401: If username or password don't match config.
    """
    user_ok = secrets.compare_digest(credentials.username, config.basic_auth_user)
    pass_ok = secrets.compare_digest(credentials.password, config.basic_auth_pass)
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="Invalid credentials")


@router.get("/v1/health")
async def health() -> dict:
    """
    Health check endpoint. No authentication required.

    Returns:
        {"ok": true} if the service is running.
    """
    return {"ok": True}


@router.post("/v1/message", dependencies=[Depends(verify_auth)])
async def post_message(req: MessageRequest) -> dict:
    """
    Add a message to the buffer.

    If the buffer is IDLE or BUFFERING, the message goes to current_buffer
    and the timer starts/resets. If PROCESSING, it goes to pending_buffer.

    Args:
        req: MessageRequest with non-empty text field.

    Returns:
        {"ok": true} on success.
    """
    await buffer.add_message(req.text)
    return {"ok": True}


@router.delete("/v1/session", dependencies=[Depends(verify_auth)])
async def delete_session() -> dict:
    """
    Reset the Claude session (clear session_id).

    The next Claude request will create a fresh session.
    Buffers and history are NOT cleared.

    Returns:
        {"ok": true} on success.
    """
    buffer.delete_session()
    return {"ok": True}


@router.get("/v1/status", dependencies=[Depends(verify_auth)])
async def get_status() -> StatusResponse:
    """
    Get the current buffer state, contents, and timer status.

    Returns:
        StatusResponse with state, buffer contents, timer remainders,
        and current session_id.
    """
    return StatusResponse(
        state=buffer.state.value,
        current_buffer=list(buffer.current_buffer),
        pending_buffer=list(buffer.pending_buffer),
        timer_remaining=buffer.timer_remaining(),
        pending_timer_remaining=buffer.pending_timer_remaining(),
        session_id=buffer.session_id,
    )


@router.get("/v1/history", dependencies=[Depends(verify_auth)])
async def get_history() -> HistoryResponse:
    """
    Get the history of all sent message batches.

    Returns:
        HistoryResponse with list of BatchRecord entries sorted by id.
    """
    return HistoryResponse(batches=buffer.history)


@router.get("/v1/response", dependencies=[Depends(verify_auth)])
async def get_response() -> ResponseStatus:
    """
    Poll the current Claude response (if processing).

    Returns:
        ResponseStatus with active=true and accumulated text/tool_calls
        if Claude is currently processing, or active=false otherwise.
    """
    from app.models import BufferState as BS

    if buffer.state == BS.PROCESSING and buffer.current_response is not None:
        return ResponseStatus(
            active=True,
            text=buffer.current_response,
            tool_calls=[
                ToolCall(
                    tool=tc.tool,
                    input=tc.input,
                    result=tc.result,
                    timestamp=tc.timestamp,
                )
                for tc in buffer.current_tool_calls
            ],
            started_at=buffer.processing_started_at,
        )

    return ResponseStatus(active=False)
