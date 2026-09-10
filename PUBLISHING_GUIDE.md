# 📘 AI生成プロジェクトの安全公開・完全匿名化ガイド (Safe Publishing Guide)

> AIエージェント（OpenClaw / Claude Code / Antigravity等）と対話しながら作成したローカルスクリプトや成果物を、**個人情報・ローカル固有パス・コミット履歴を完全に秘匿・一般化してGitHubおよびGitHub Pagesへ安全に公開するための実践ワークフロー**です。

---

## ⚠️ なぜAI開発プロジェクトで匿名化が必要なのか？

AIエージェントにコード作成やバッチ処理を依頼すると、以下の「個人・環境特定情報」が無意識のうちにコード内に混入しがちです。

1. **ローカル環境の絶対パス**  
   （例: `/path/to/<username>/...`, `C:\Users\<username>\...`, `クラウド同期フォルダ`）
2. **Gitコミットの作者情報（Author）**  
   （PCのデフォルト設定やOSユーザー名から、本名や私用メールアドレス `user@example.com` がコミットログに永久刻印される）
3. **作業ログや中間データベース**  
   （実在の個人ファイル名、病名・患者情報、組織内の機密データを含む `.sqlite` や `.csv`, `.log`）

これらを公開前に徹底的に除去・一般化する手順を以下に体系化しています。

---

## 🛠️ 6ステップ完全公開ワークフロー

### ステップ 1: プロジェクトの分離と `.gitignore` の策定

作業フォルダ（エージェントの作業領域）から、公開に必要なスクリプト・ドキュメントのみを別ディレクトリ（リポジトリルート）に抽出・整理します。

```bash
mkdir -p my-project/scripts my-project/docs
```

`.gitignore` で、機密データや個人環境の一時ファイルが誤コミットされるのを確実に防ぎます：

```gitignore
# 個人データ・データベース・実行ログ（絶対コミット禁止）
data/
*.sqlite
*.sqlite-shm
*.sqlite-wal
*.csv
*.json
*.log
*.pid

# Python キャッシュ & 仮想環境
__pycache__/
*.py[cod]
.venv/
.env
.DS_Store
```

---

### ステップ 2: コード・設定ファイルの静的監査とパスの相対化

コード内にハードコードされた絶対パスを、相対パス（実行時ディレクトリ基準）や引数デフォルト値に置き換えます。

* **Before (危険):**
  ```python
  parser.add_argument("--target-dir", default="/path/to/my_private_docs")
  parser.add_argument("--db-path", default="/path/to/data/progress.sqlite")
  ```
* **After (安全・一般化):**
  ```python
  parser.add_argument("--target-dir", default="./input_files", help="Target directory")
  parser.add_argument("--db-path", default="./data/progress.sqlite", help="SQLite DB path")
  ```

シェルスクリプト内のパスも、スクリプト自身の配置位置からの相対パスに統一します：
```bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$DIR/../data/batch.log"
```

---

### ステップ 3: Git初期化と「匿名コミット情報」のローカル設定

グローバルなGit設定（本名や個人アドレス）をリポジトリに反映させないよう、**リポジトリローカルの設定としてGitHub公式の匿名メールアドレスを設定**します。

```bash
cd my-project
git init -b main

# GitHubアカウント名と noreply メールを設定（ローカルリポジトリのみ適用）
git config user.name "<GitHubユーザー名>"
git config user.email "<GitHubユーザー名>@users.noreply.github.com"
```

---

### ステップ 4: GitHub Pages（Webサイト）の構築

リポジトリ直下の `docs/index.html` にランディングページを配置することで、無料かつノーコードで解説サイトを世界中に公開できます。

* **推奨構成:**
  * CDN経由の Tailwind CSS でモダンかつ軽量なデザイン
  * 開発の背景、課題と解決策、Before / After の実例比較
  * AIエージェントに確実に指示するためのプロンプト集

---

### ステップ 5: 自動プライバシー監査（Pre-flight Check）

コミット前に、本名やローカルパス、メールアドレスが残っていないか自動スクリプトで監査します。

```bash
./scripts/check_anonymity.sh
```

または以下のワンライナーで検索:
```bash
grep -rnI -E "home/|mnt/|@gmail|cloud_drive|username" . --exclude-dir=.git
```

問題がなければコミットを作成します：
```bash
git add .
git commit -m "Initial commit: Open-source release with GitHub Pages"
```

---

### ステップ 6: SSH認証と安全なGitHubプッシュ

GitHubへ安全にプッシュするために、SSH鍵を使用します（パスワード不要）。

```bash
# 鍵が存在しない場合は生成
ssh-keygen -t ed25519 -C "anon@github" -f ~/.ssh/id_ed25519 -N ""

# 公開鍵を表示して GitHub Settings -> SSH and GPG keys に登録
cat ~/.ssh/id_ed25519.pub

# リモートを設定してプッシュ
git remote add origin git@github.com:<GitHubユーザー名>/<リポジトリ名>.git
git push -u origin main
```

---

## 🚨 万が一、個人情報を含んだままプッシュしてしまった場合の対処法

GitHubにプッシュした後で個人情報や本名コミットに気付いた場合でも、**直後のコミットであれば完全消去（履歴上書き）が可能**です。

1. ファイル内の該当箇所を修正します。
2. Gitのコミット作成者を匿名アドレスで上書き（amend）します：
   ```bash
   git config user.name "<GitHubユーザー名>"
   git config user.email "<GitHubユーザー名>@users.noreply.github.com"
   git add -A
   git commit --amend --reset-author -m "Initial commit: Open-source release"
   ```
3. **強制プッシュ（force push）** でGitHub上の履歴を上書きします：
   ```bash
   git push --force origin main
   ```
   * これにより、GitHub上のコミット履歴からも以前のコミットハッシュおよび個人情報が完全に抹消されます。

---

## ✅ 公開前チェックリスト

- [ ] `.gitignore` でログ、DB（sqlite）、CSV等の実データが除外されているか？
- [ ] スクリプト内のパスに `/home/...` や `/mnt/...` などのローカル絶対パスが残っていないか？
- [ ] `LICENSE` や `README.md` の著作者名がハンドルネーム（GitHub名）になっているか？
- [ ] `git log` の Author に個人メールアドレス（`@gmail.com` 等）が含まれていないか？
- [ ] `./scripts/check_anonymity.sh` を実行してエラーが出ないか？
