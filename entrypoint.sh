#!/bin/sh
# Copy example configs if user configs don't exist yet.
# This ensures git pull updates examples but never overwrites user files.

if [ ! -f /config/system-prompt.md ]; then
  cp /app/config/system-prompt.md.example /config/system-prompt.md
  echo "[entrypoint] Created /config/system-prompt.md from example"
fi

exec "$@"
