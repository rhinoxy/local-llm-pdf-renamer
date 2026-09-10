#!/bin/bash
# run_papers_batch.sh - 論文PDF解析バッチをバックグラウンドサービスとして実行

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 既存サービス停止
systemctl --user stop paper-rename 2>/dev/null || true
systemctl --user reset-failed paper-rename 2>/dev/null || true

echo "Starting paper rename background service..."
systemd-run --user --unit=paper-rename \
  --description="Paper PDF Rename Batch Job" \
  /usr/bin/python3 -u "$DIR/rename_papers.py" "$@"

echo ""
echo "Service started successfully as 'paper-rename.service'."
echo "To view live logs: journalctl --user -u paper-rename -f"
echo "To check status:   $DIR/status_papers_batch.sh"
echo "To stop:           systemctl --user stop paper-rename"
