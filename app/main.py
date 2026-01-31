"""
Application entrypoint for claudecode2api-buffer.

Creates the FastAPI app, configures logging, registers routes,
and handles graceful shutdown of buffer timers.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.api import router
from app.config import config


def setup_logging() -> None:
    """
    Configure structured logging to stdout.

    Sets up the 'buffer' logger with a timestamp-prefixed format
    matching the specification: [YYYY-MM-DD HH:MM:SS] EVENT: description
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger = logging.getLogger("buffer")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.

    On startup: logs the listening port.
    On shutdown: cancels any active buffer timers for clean exit.
    """
    logger = logging.getLogger("buffer")
    logger.info("STARTUP: listening on port %d", config.port)
    yield
    # Graceful shutdown: cancel timers
    from app.buffer import buffer
    buffer._cancel_timer()
    buffer._cancel_pending_timer()
    logger.info("SHUTDOWN: cleanup complete")


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI instance with routes and lifespan manager.
    """
    setup_logging()
    app = FastAPI(
        title="claudecode2api-buffer",
        description="Message buffer for Claude Code API",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=config.port,
        log_level="warning",
    )
