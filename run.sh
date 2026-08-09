#!/bin/bash
# Convenience wrapper: collect, commit, push (for cron/systemd use)
set -e
cd "$(dirname "$0")"
python3 collector.py
if [ -n "$(git status --porcelain)" ]; then
  git add -A
  git commit -m "chore: auto-refresh ecosystem report $(date -u +%FT%TZ)"
  git push -q || true
fi
