#!/bin/zsh
# Import (or re-import) the 8 workflows into the n8n container started by docker-compose.
# Workflow IDs are stable (AudioStoryWF0000…0700), so re-importing updates them in place.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T n8n n8n import:workflow --separate --input=/workflows
docker compose exec -T n8n n8n list:workflow
echo "Next: in the n8n editor create credentials 'ASS Webhook Auth' (Header Auth) and"
echo "'Audio Story Bot' (Telegram API), then publish the workflows you want active."
