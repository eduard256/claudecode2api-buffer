"""
Configuration module for claudecode2api-buffer.

Loads all settings from environment variables with sensible defaults.
All Claude API connection details, buffer timing, auth credentials,
and tool configurations are managed here.
"""

import json
import os
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class Config:
    """Application configuration loaded from environment variables."""

    # Server
    port: int = int(os.getenv("PORT", "3856"))
    basic_auth_user: str = os.getenv("BASIC_AUTH_USER", "admin")
    basic_auth_pass: str = os.getenv("BASIC_AUTH_PASS", "password")

    # Buffer
    buffer_timeout: int = int(os.getenv("BUFFER_TIMEOUT", "25"))

    # Claude API
    claude_api_url: str = os.getenv("CLAUDE_API_URL", "http://localhost:9876")
    claude_api_user: str = os.getenv("CLAUDE_API_USER", "")
    claude_api_pass: str = os.getenv("CLAUDE_API_PASS", "")

    # Workspace
    workspace_dir: str = os.getenv("WORKSPACE_DIR", "/home/user/menu-workspace")

    # System prompt
    system_prompt_file: str = os.getenv("SYSTEM_PROMPT_FILE", "/config/system-prompt.md")

    # Tools (JSON arrays from env)
    claude_tools: list[str] = field(default_factory=list)
    claude_allowed_tools: list[str] = field(default_factory=list)

    # Session persistence
    session_file: str = os.getenv("SESSION_FILE", "/data/session.json")

    def __post_init__(self) -> None:
        """Parse JSON arrays from environment variables after init."""
        tools_raw = os.getenv("CLAUDE_TOOLS", '["Bash"]')
        allowed_raw = os.getenv("CLAUDE_ALLOWED_TOOLS", '["Bash"]')
        try:
            self.claude_tools = json.loads(tools_raw)
        except json.JSONDecodeError:
            self.claude_tools = ["Bash"]
        try:
            self.claude_allowed_tools = json.loads(allowed_raw)
        except json.JSONDecodeError:
            self.claude_allowed_tools = ["Bash"]

    def load_system_prompt(self) -> str | None:
        """
        Read system prompt from the configured file path.

        Returns:
            The contents of the system prompt file, or None if the file
            does not exist or cannot be read.
        """
        path = Path(self.system_prompt_file)
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return None


config = Config()
