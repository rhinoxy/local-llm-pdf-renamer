#!/bin/bash
# status_papers_batch.sh - 論文バッチ処理の進捗確認スクリプト

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_PATH="$DIR/../data/paper_rename_progress.sqlite"

echo "=== Paper Rename Batch Progress ==="
systemctl --user status paper-rename --no-pager 2>/dev/null | grep -E "Active:|Tasks:|Main PID:" || echo "Service status: stopped/inactive"
echo ""

if [ -f "$DB_PATH" ]; then
  python3 -c "
import sqlite3
conn = sqlite3.connect('$DB_PATH')
cur = conn.cursor()
cur.execute('SELECT status, count(*) FROM paper_analysis GROUP BY status')
rows = cur.fetchall()
total = sum(r[1] for r in rows)
print(f'Total analyzed: {total} / 498 target files')
for status, cnt in rows:
    print(f'  - {status}: {cnt}')

print('\nRecent 5 entries:')
cur.execute('SELECT filename, target_filename, status FROM paper_analysis ORDER BY updated_at DESC LIMIT 5')
for fn, target, st in cur.fetchall():
    print(f'  [{st}] {fn} -> {target}')
"
else
  echo "No database found yet."
fi

echo ""
echo "Recent logs (last 5 lines):"
journalctl --user -u paper-rename -n 5 --no-pager 2>/dev/null
