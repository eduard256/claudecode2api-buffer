"""
Message buffer with state machine for claudecode2api-buffer.

Implements the core buffering logic: accumulates incoming messages,
manages timers, transitions between IDLE/BUFFERING/PROCESSING states,
queues pending batches during active Claude processing, and dispatches
them sequentially after the current request completes.

Thread-safety is achieved via asyncio locks since all operations
run in a single event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.config import config
from app.models import BatchRecord, BufferState, ToolCall
from app import claude_client

logger = logging.getLogger("buffer")


class MessageBuffer:
    """
    Central state machine managing message buffering and Claude API dispatch.

    Maintains a current buffer for accumulating messages, a pending buffer
    for messages arriving during PROCESSING, and a dispatch queue for
    batches waiting to be sent. Claude always runs to completion -- no
    cancellation. Queued batches dispatch sequentially.

    Attributes:
        state: Current state of the buffer (IDLE, BUFFERING, PROCESSING).
        current_buffer: Messages waiting to be sent in the current batch.
        pending_buffer: Messages accumulating while Claude is processing.
        history: List of completed batch records.
        current_response: Accumulated response from the active Claude call.
        current_tool_calls: Tool invocations from the active Claude call.
        processing_started_at: Timestamp when current processing began.
    """

    def __init__(self) -> None:
        self.state = BufferState.IDLE
        self.current_buffer: list[str] = []
        self.pending_buffer: list[str] = []
        self._pending_queue: list[list[str]] = []
        self._timer: asyncio.TimerHandle | None = None
        self._pending_timer: asyncio.TimerHandle | None = None
        self._timer_deadline: float | None = None
        self._pending_timer_deadline: float | None = None
        self._lock = asyncio.Lock()
        self._batch_counter = 0
        self.history: list[BatchRecord] = []
        self.current_response: str | None = None
        self.current_tool_calls: list[ToolCall] = []
        self.processing_started_at: str | None = None
        self._processing_task: asyncio.Task | None = None

    # -- Session persistence ------------------------------------------------

    def _load_session_id(self) -> str | None:
        """
        Load session_id from the persistent JSON file.

        Returns:
            The stored session_id string, or None if not found or on error.
        """
        path = Path(config.session_file)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data.get("session_id")
            except (json.JSONDecodeError, OSError):
                pass
        return None

    def _save_session_id(self, session_id: str) -> None:
        """
        Persist session_id to the JSON file, creating parent dirs if needed.

        Args:
            session_id: The session identifier to save.
        """
        path = Path(config.session_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"session_id": session_id}),
            encoding="utf-8",
        )

    def delete_session(self) -> None:
        """
        Remove the persisted session_id file, causing the next request
        to create a fresh Claude session.
        """
        path = Path(config.session_file)
        if path.exists():
            path.unlink()
        logger.info("SESSION RESET")

    @property
    def session_id(self) -> str | None:
        """Current session_id from persistent storage."""
        return self._load_session_id()

    # -- Timer helpers ------------------------------------------------------

    def _start_timer(self) -> None:
        """Start or reset the main buffer timer to BUFFER_TIMEOUT seconds."""
        self._cancel_timer()
        loop = asyncio.get_event_loop()
        self._timer_deadline = loop.time() + config.buffer_timeout
        self._timer = loop.call_later(
            config.buffer_timeout,
            lambda: asyncio.ensure_future(self._on_timer_expired()),
        )
        logger.info("TIMER START: %d sec", config.buffer_timeout)

    def _cancel_timer(self) -> None:
        """Cancel the main buffer timer if running."""
        if self._timer:
            self._timer.cancel()
            self._timer = None
            self._timer_deadline = None

    def _start_pending_timer(self) -> None:
        """Start or reset the pending buffer timer to BUFFER_TIMEOUT seconds."""
        self._cancel_pending_timer()
        loop = asyncio.get_event_loop()
        self._pending_timer_deadline = loop.time() + config.buffer_timeout
        self._pending_timer = loop.call_later(
            config.buffer_timeout,
            lambda: asyncio.ensure_future(self._on_pending_timer_expired()),
        )
        logger.info("PENDING TIMER START: %d sec", config.buffer_timeout)

    def _cancel_pending_timer(self) -> None:
        """Cancel the pending buffer timer if running."""
        if self._pending_timer:
            self._pending_timer.cancel()
            self._pending_timer = None
            self._pending_timer_deadline = None

    def timer_remaining(self) -> float | None:
        """
        Seconds remaining on the main buffer timer.

        Returns:
            Remaining seconds as float, or None if timer is not active.
        """
        if self._timer_deadline is None:
            return None
        loop = asyncio.get_event_loop()
        remaining = self._timer_deadline - loop.time()
        return max(0.0, remaining)

    def pending_timer_remaining(self) -> float | None:
        """
        Seconds remaining on the pending buffer timer.

        Returns:
            Remaining seconds as float, or None if timer is not active.
        """
        if self._pending_timer_deadline is None:
            return None
        loop = asyncio.get_event_loop()
        remaining = self._pending_timer_deadline - loop.time()
        return max(0.0, remaining)

    # -- State transitions --------------------------------------------------

    def _set_state(self, new_state: BufferState) -> None:
        """
        Transition to a new state with logging.

        Args:
            new_state: The target BufferState to transition to.
        """
        old = self.state
        self.state = new_state
        logger.info("STATE: %s -> %s", old.value, new_state.value)

    # -- Public: add message ------------------------------------------------

    async def add_message(self, text: str) -> None:
        """
        Add a message to the appropriate buffer based on current state.

        If IDLE or BUFFERING: adds to current_buffer and starts/resets the main timer.
        If PROCESSING: adds to pending_buffer and starts/resets the pending timer.

        Args:
            text: The message text to buffer.
        """
        async with self._lock:
            if self.state in (BufferState.IDLE, BufferState.BUFFERING):
                self.current_buffer.append(text)
                self._start_timer()
                if self.state == BufferState.IDLE:
                    self._set_state(BufferState.BUFFERING)
                else:
                    logger.info("TIMER RESET: %d sec", config.buffer_timeout)
                logger.info("MESSAGE IN: %s", text[:100])
            elif self.state == BufferState.PROCESSING:
                self.pending_buffer.append(text)
                self._start_pending_timer()
                logger.info("MESSAGE IN (pending): %s", text[:100])
                logger.info("PENDING TIMER RESET: %d sec", config.buffer_timeout)

    # -- Timer callbacks ----------------------------------------------------

    async def _on_timer_expired(self) -> None:
        """
        Called when the main buffer timer expires.

        Takes the current_buffer messages, clears the buffer, transitions
        to PROCESSING, and dispatches to Claude in a background task.
        """
        async with self._lock:
            self._timer = None
            self._timer_deadline = None
            if not self.current_buffer:
                self._set_state(BufferState.IDLE)
                return

            messages = list(self.current_buffer)
            self.current_buffer.clear()
            logger.info("TIMER EXPIRED: sending %d messages", len(messages))
            self._set_state(BufferState.PROCESSING)

        await self._send_to_claude(messages)

    async def _on_pending_timer_expired(self) -> None:
        """
        Called when the pending buffer timer expires during PROCESSING.

        Moves pending_buffer messages into the dispatch queue. If Claude
        has already finished (task is done), dispatches immediately.
        Otherwise the batch waits in queue for _after_processing to pick up.
        """
        send_now = None
        async with self._lock:
            self._pending_timer = None
            self._pending_timer_deadline = None
            if not self.pending_buffer:
                return

            messages = list(self.pending_buffer)
            self.pending_buffer.clear()

            # If Claude already finished, dispatch immediately
            task_done = self._processing_task is None or self._processing_task.done()
            if task_done:
                logger.info("PENDING BATCH READY: %d messages, claude idle, sending now",
                            len(messages))
                self._set_state(BufferState.PROCESSING)
                send_now = messages
            else:
                self._pending_queue.append(messages)
                logger.info("PENDING BATCH QUEUED: %d messages (queue depth: %d)",
                            len(messages), len(self._pending_queue))

        if send_now:
            await self._send_to_claude(send_now)

    # -- Claude dispatch ----------------------------------------------------

    async def _send_to_claude(self, messages: list[str]) -> None:
        """
        Send a batch of messages to Claude and handle the response.

        Joins messages with newlines, sends via claude_client.send_chat(),
        saves the session_id, records the batch in history, and transitions
        back to IDLE or dispatches the next queued batch.

        Args:
            messages: List of message strings to send as a single prompt.
        """
        prompt = "\n".join(messages)
        session_id = self._load_session_id()

        self._batch_counter += 1
        batch = BatchRecord(
            id=self._batch_counter,
            messages=messages,
            sent_at=datetime.now(timezone.utc).isoformat(),
        )
        self.history.append(batch)

        # Reset response accumulator
        self.current_response = ""
        self.current_tool_calls = []
        self.processing_started_at = datetime.now(timezone.utc).isoformat()

        async def _run() -> None:
            try:
                result = await claude_client.send_chat(
                    prompt=prompt,
                    session_id=session_id,
                    on_text=lambda t: self._on_claude_text(t),
                    on_tool_use=lambda tool, inp: self._on_claude_tool(tool, inp),
                )
                # Save session_id
                if result.session_id:
                    self._save_session_id(result.session_id)

                # Update batch record
                batch.completed_at = datetime.now(timezone.utc).isoformat()
                batch.response = result.text

            except Exception as e:
                logger.error("CLAUDE ERROR (batch #%d): %s", batch.id, e)
                batch.response = f"Error: {e}"
                batch.completed_at = datetime.now(timezone.utc).isoformat()
            finally:
                await self._after_processing()

        self._processing_task = asyncio.create_task(_run())

    def _on_claude_text(self, text: str) -> None:
        """Callback: accumulate text from Claude's streaming response."""
        if self.current_response is None:
            self.current_response = ""
        self.current_response += text

    def _on_claude_tool(self, tool: str, input_str: str) -> None:
        """Callback: record a tool call from Claude's response."""
        self.current_tool_calls.append(ToolCall(
            tool=tool,
            input=input_str,
        ))

    async def _after_processing(self) -> None:
        """
        Post-processing after Claude finishes.

        Checks the dispatch queue and pending buffer to decide next action:
        1. Queue has batches -> pop first and send immediately.
        2. Queue empty but pending_buffer has messages (timer running) -> stay
           PROCESSING and wait for the pending timer to queue them.
        3. Everything empty -> transition to IDLE.
        """
        async with self._lock:
            self._processing_task = None
            self.current_response = None
            self.current_tool_calls = []
            self.processing_started_at = None

            # Dispatch next queued batch if available
            if self._pending_queue:
                messages = self._pending_queue.pop(0)
                logger.info("QUEUE DISPATCH: %d messages (remaining in queue: %d)",
                            len(messages), len(self._pending_queue))
                self._set_state(BufferState.PROCESSING)
                # Schedule send outside of lock via ensure_future
                asyncio.ensure_future(self._send_to_claude(messages))
                return

            # Pending buffer still accumulating (timer running), wait for it
            if self.pending_buffer:
                logger.info("PENDING BUFFER ACTIVE: %d messages, waiting for timer",
                            len(self.pending_buffer))
                return

            self._set_state(BufferState.IDLE)


# Singleton instance used across the application
buffer = MessageBuffer()
