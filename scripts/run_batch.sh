#!/bin/bash
# run_batch.sh - systemd-run --user を使ってバックグラウンドで完全自律実行するスクリプト

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$DIR/../data/batch.log"

# 既存のユニットがあれば停止
systemctl --user stop magazine-rename 2>/dev/null || true
systemctl --user reset-failed magazine-rename 2>/dev/null || true

echo "Starting magazine rename background service..."
systemd-run --user --unit=magazine-rename \
  --description="Magazine PDF Rename Batch Job" \
  /usr/bin/python3 -u "$DIR/rename_magazines.py" "$@"

echo ""
echo "Service started successfully as 'magazine-rename.service'."
echo "To view live logs: journalctl --user -u magazine-rename -f"
echo "To check status:   systemctl --user status magazine-rename"
echo "To stop:           systemctl --user stop magazine-rename"
