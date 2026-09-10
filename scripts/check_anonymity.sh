#!/bin/bash
# check_anonymity.sh - 公開前に個人情報や環境固有パスが残っていないか自動監査するスクリプト

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

echo "=== 🔒 Anonymity & Privacy Pre-flight Check ==="

SUSPICIOUS_PATTERNS=(
  "/home/[a-zA-Z0-9_-]+"
  "/mnt/[a-zA-Z0-9_-]+"
  "[a-zA-Z0-9_.+-]+@gmail\.com"
  "[a-zA-Z0-9_.+-]+@yahoo\.[a-zA-Z.]+"
  "OneDrive"
  "Precision"
)

FOUND=0

for pattern in "${SUSPICIOUS_PATTERNS[@]}"; do
  MATCHES=$(grep -rnI -E "$pattern" . --exclude-dir=.git --exclude-dir=data --exclude="check_anonymity.sh" 2>/dev/null || true)
  if [ -n "$MATCHES" ]; then
    echo "⚠️  Found potential private information matching pattern: $pattern"
    echo "$MATCHES"
    echo ""
    FOUND=1
  fi
done

# Git Author Check
GIT_AUTHOR=$(git log -n 1 --format="%an <%ae>" 2>/dev/null || true)
echo "Current Git Commit Author: $GIT_AUTHOR"
if [[ "$GIT_AUTHOR" =~ "noreply.github.com" ]]; then
  echo "✅ Git Author is safely anonymized using GitHub noreply address."
else
  echo "⚠️  Warning: Git author may contain personal email. Consider running: git commit --amend --reset-author"
fi

echo ""
if [ $FOUND -eq 0 ]; then
  echo "✅ All files passed privacy check. Safe to publish!"
else
  echo "❌ Privacy check flagged potential sensitive info. Please inspect above."
fi
