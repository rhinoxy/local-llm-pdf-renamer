#!/usr/bin/env python3
"""
rename_papers.py
対象ディレクトリ配下の「内容と無関係なファイル名」のPDFを自動検出し、
ローカルLLM/Vision（Gemma 4）を用いて【論文タイトル_第一著者.pdf】へリネームするスクリプト。
"""

import os
import sys
import glob
import json
import sqlite3
import argparse
import base64
import re
import csv
import time
import requests
import pymupdf

OLLAMA_API_URL = "http://127.0.0.1:11434/api/chat"
MODEL_NAME = "gemma4:latest"

# 無関係なファイル名と判定する正規表現パターン
GENERIC_PATTERNS = [
    r'^download(\s*\(\d+\))?$',
    r'^\d+_\d+.*$',                # 例: 110_727, 64_683, 22_50-1
    r'^新規ドキュメント\d*$',
    r'^Image[_\s-]?\d+.*$',
    r'^Document[_\s-]?\d+.*$',
    r'^Scan(ned)?[_\s-]?\d*$',
    r'^\d{5,}$',                   # 5桁以上の数字のみ
    r'^[0-9a-fA-F\-]{8,}$',        # ハッシュやUUID
    r'^(untitled|fulltext|article|paper|content|document)[\d_\-]*$',
    r'^[a-zA-Z0-9_\-]{1,6}$',      # 6文字以下の短いコード
]

def is_generic_filename(filename: str) -> bool:
    """ファイル名が内容と無関係（機械的・デフォルト名）かを判定"""
    base, ext = os.path.splitext(filename)
    base_clean = base.strip()
    
    for pat in GENERIC_PATTERNS:
        if re.match(pat, base_clean, re.IGNORECASE):
            return True
            
    # 日本語が含まれず、かつ英単語が2語以下の非常に短いもの
    has_japanese = bool(re.search(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]', base_clean))
    words = re.findall(r'[A-Za-z0-9]+', base_clean)
    if not has_japanese and len(words) <= 2 and len(base_clean) < 16:
        return True
        
    return False

def sanitize_filename(name: str) -> str:
    """ファイル名として安全な文字列にサニタイズ"""
    if not name:
        return ""
    # Gemmaなどのバイトシーケンス表記 <0x..> を除去
    name = re.sub(r'<0x[0-9a-fA-F]{2}>', '', name)
    # 改行やタブ
    name = re.sub(r'[\r\n\t]+', ' ', name)
    # 禁止文字置換
    name = re.sub(r'[\/\\:\*\?"<>\|]', '_', name)
    # 連続スペース・アンダースコア整理
    name = re.sub(r'\s+', ' ', name)
    name = re.sub(r'_+', '_', name)
    name = name.strip(' ._')
    
    # UTF-8バイト長制限（最大180バイト程度に切り詰め）
    encoded = name.encode('utf-8')
    if len(encoded) > 180:
        name = encoded[:175].decode('utf-8', errors='ignore')
    return name

def init_db(db_path: str):
    """進捗・マッピング管理用SQLiteデータベースの初期化"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS paper_analysis (
            filepath TEXT PRIMARY KEY,
            relpath TEXT,
            filename TEXT,
            status TEXT,  -- 'ready', 'skipped_descriptive', 'error', 'applied'
            title TEXT,
            first_author TEXT,
            journal TEXT,
            year TEXT,
            original_filename TEXT,
            target_filename TEXT,
            extraction_mode TEXT,  -- 'text' or 'vision'
            applied_at TEXT,
            error_message TEXT,
            raw_response TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    return conn

def extract_metadata_from_pdf(filepath: str) -> dict:
    """PDFの先頭ページからタイトルと筆頭著者を抽出（テキストまたはVision）"""
    try:
        doc = pymupdf.open(filepath)
        if doc.is_encrypted:
            return {"status": "error", "error_message": "Document is password encrypted"}
        if len(doc) == 0:
            return {"status": "error", "error_message": "Empty PDF"}
        first_page = doc[0]
        txt = first_page.get_text().strip()
    except Exception as e:
        return {"status": "error", "error_message": f"Cannot open or read PDF: {e}"}
    except (ValueError, RuntimeError) as e:
        return {"status": "error", "error_message": f"PDF error (encrypted/corrupt): {e}"}
    
    # 1. テキスト抽出優先（意味のある文字が十分にあるかチェック）
    valid_chars = re.findall(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ffA-Za-z0-9]', txt)
    if len(valid_chars) > 60:
        # 先頭2000文字程度を利用
        sample_txt = txt[:2000]
        prompt = f"""以下は学術論文・医学資料の第1ページのテキストです。
論文タイトル（正式名）と筆頭著者（第一著者の苗字または氏名）を抽出してください。
【入力テキスト】
{sample_txt}

必ず以下のJSON形式のみを出力してください:
{{
  "title": "論文タイトル（日本語または英語の正式名）",
  "first_author": "筆頭著者名（例: 鈴木, Smith等。不明ならnull）",
  "journal": "掲載誌名（不明ならnull）",
  "year": "発行年（不明ならnull）"
}}"""
        try:
            res = requests.post(OLLAMA_API_URL, json={
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}],
                "format": "json",
                "stream": False,
                "options": {"temperature": 0.1}
            }, timeout=60)
            if res.status_code == 200:
                data = json.loads(res.json().get("message", {}).get("content", "{}"))
                if data.get("title"):
                    return process_extracted_data(data, mode="text")
        except Exception as e:
            pass  # Visionにフォールバック

    # 2. Vision（スキャン画像またはテキスト不十分）
    try:
        pix = first_page.get_pixmap(dpi=140)
        b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
        prompt = """この画像は学術論文・資料の第1ページまたは表紙です。
論文タイトル（正式名）と筆頭著者名を抽出してください。
必ず以下のJSON形式のみを出力してください:
{
  "title": "論文タイトル",
  "first_author": "筆頭著者名（不明ならnull）",
  "journal": "雑誌名や学会名（不明ならnull）",
  "year": "発行年（不明ならnull）"
}"""
        res = requests.post(OLLAMA_API_URL, json={
            "model": MODEL_NAME,
            "messages": [{"role": "user", "content": prompt, "images": [b64]}],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.1}
        }, timeout=90)
        if res.status_code == 200:
            data = json.loads(res.json().get("message", {}).get("content", "{}"))
            return process_extracted_data(data, mode="vision")
    except Exception as e:
        return {"status": "error", "error_message": f"Vision extraction failed: {e}"}

    return {"status": "error", "error_message": "Failed to parse response"}

def process_extracted_data(data: dict, mode: str) -> dict:
    """抽出データから案Cのファイル名を生成"""
    title = data.get("title") or ""
    author = data.get("first_author") or ""
    journal = data.get("journal") or ""
    year = data.get("year") or ""
    
    # 文字列クリーンアップ
    def clean(s):
        if not s or str(s).lower() in ("null", "none", "unknown"):
            return ""
        return str(s).strip()
        
    title = clean(title)
    author = clean(author)
    
    if not title:
        return {
            "status": "error",
            "error_message": "No title extracted",
            "raw_response": json.dumps(data, ensure_ascii=False)
        }
        
    # 案C: {論文タイトル}_{第一著者}.pdf
    if author:
        base_name = f"{title}_{author}"
    else:
        base_name = title
        
    clean_name = sanitize_filename(base_name)
    target_filename = f"{clean_name}.pdf" if clean_name else ""
    
    return {
        "status": "ready" if target_filename else "error",
        "title": title,
        "first_author": author,
        "journal": clean(journal),
        "year": clean(year),
        "target_filename": target_filename,
        "extraction_mode": mode,
        "raw_response": json.dumps(data, ensure_ascii=False)
    }

def export_reports(conn: sqlite3.Connection, output_dir: str):
    """結果をCSVおよびJSONでエクスポート"""
    cur = conn.cursor()
    cur.execute("SELECT * FROM paper_analysis WHERE status != 'skipped_descriptive' ORDER BY relpath ASC, filename ASC")
    rows = cur.fetchall()
    cols = [desc[0] for desc in cur.description]
    
    csv_path = os.path.join(output_dir, "paper_rename_report.csv")
    json_path = os.path.join(output_dir, "paper_rename_report.json")
    
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        for r in rows:
            writer.writerow(r)
            
    print(f"[Export] Report saved to: {csv_path}")

def run_dry_run(root_dir: str, db_path: str, limit: int = None):
    """対象PDFを走査し、無関係ファイル名のもののみ解析してDBに保存"""
    conn = init_db(db_path)
    cur = conn.cursor()
    
    all_targets = []
    skipped_descriptive = 0
    
    print(f"Scanning directory: {root_dir} (excluding 99 Zotero) ...")
    for dirpath, dirnames, filenames in os.walk(root_dir):
        rel = os.path.relpath(dirpath, root_dir)
        if any("zotero" in p.lower() for p in rel.split(os.sep)):
            continue
            
        for f in filenames:
            if not f.lower().endswith(".pdf"):
                continue
            filepath = os.path.join(dirpath, f)
            
            if is_generic_filename(f):
                all_targets.append((filepath, rel, f))
            else:
                skipped_descriptive += 1
                
    total_targets = len(all_targets)
    print(f"=== [Dry-Run] Target Files to Rename: {total_targets} (Already descriptive/skipped: {skipped_descriptive}) ===")
    
    processed_count = 0
    already_analyzed = 0
    
    for idx, (filepath, relpath, filename) in enumerate(all_targets, 1):
        if limit and processed_count >= limit:
            print(f"[Limit reached: {limit} files]")
            break
            
        cur.execute("SELECT status, target_filename FROM paper_analysis WHERE filepath = ?", (filepath,))
        row = cur.fetchone()
        if row and row[0] in ("ready", "applied"):
            already_analyzed += 1
            continue
            
        print(f"[{idx}/{total_targets}] [{relpath}] {filename} ... ", end="", flush=True)
        t0 = time.time()
        res = extract_metadata_from_pdf(filepath)
        elapsed = time.time() - t0
        
        status = res.get("status", "error")
        target_name = res.get("target_filename", "")
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        
        cur.execute("""
            INSERT OR REPLACE INTO paper_analysis (
                filepath, relpath, filename, status, title, first_author, journal, year,
                original_filename, target_filename, extraction_mode, applied_at,
                error_message, raw_response, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            filepath,
            relpath,
            filename,
            status,
            res.get("title"),
            res.get("first_author"),
            res.get("journal"),
            res.get("year"),
            filename,
            target_name,
            res.get("extraction_mode"),
            None,
            res.get("error_message"),
            res.get("raw_response"),
            now_str
        ))
        conn.commit()
        processed_count += 1
        
        if status == "ready":
            mode = res.get("extraction_mode", "")
            print(f"OK ({mode}, {elapsed:.1f}s) -> '{target_name}'")
        else:
            print(f"ERROR ({elapsed:.1f}s): {res.get('error_message')}")
            
    print(f"\n[Summary] Processed: {processed_count}, Skipped (Already Analyzed): {already_analyzed}")
    export_reports(conn, os.path.dirname(db_path))

def run_apply(db_path: str):
    """解析結果に基づき、各フォルダ内でリネームを実行"""
    conn = init_db(db_path)
    cur = conn.cursor()
    
    cur.execute("SELECT filepath, filename, target_filename FROM paper_analysis WHERE status = 'ready'")
    rows = cur.fetchall()
    
    if not rows:
        print("No files ready for renaming. Run dry-run first.")
        return
        
    print(f"=== [Apply Renaming] {len(rows)} files to rename ===")
    success_count = 0
    collision_count = 0
    
    for filepath, original_name, target_name in rows:
        if not os.path.exists(filepath):
            print(f"File not found: {filepath}")
            continue
            
        dirpath = os.path.dirname(filepath)
        target_path = os.path.join(dirpath, target_name)
        
        # 同名衝突回避
        if os.path.exists(target_path) and target_path != filepath:
            base, ext = os.path.splitext(target_name)
            counter = 1
            while os.path.exists(os.path.join(dirpath, f"{base}_{counter}{ext}")):
                counter += 1
            target_name = f"{base}_{counter}{ext}"
            target_path = os.path.join(dirpath, target_name)
            collision_count += 1
            
        try:
            os.rename(filepath, target_path)
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("""
                UPDATE paper_analysis 
                SET filepath = ?, filename = ?, target_filename = ?, status = 'applied', applied_at = ?, updated_at = ?
                WHERE filepath = ?
            """, (target_path, target_name, target_name, now_str, now_str, filepath))
            conn.commit()
            success_count += 1
            print(f"Renamed: {original_name} -> {target_name}")
        except Exception as e:
            print(f"Error renaming {original_name}: {e}")
            
    print(f"\n[Finished] Renamed: {success_count}, Collisions resolved: {collision_count}")
    export_reports(conn, os.path.dirname(db_path))

def run_rollback(db_path: str):
    """リネームされたファイルを元のファイル名に戻す"""
    conn = init_db(db_path)
    cur = conn.cursor()
    
    cur.execute("SELECT filepath, filename, original_filename FROM paper_analysis WHERE status = 'applied'")
    rows = cur.fetchall()
    
    if not rows:
        print("No applied renames found to roll back.")
        return
        
    print(f"=== [Rollback] Reverting {len(rows)} files to original names ===")
    reverted_count = 0
    
    for current_path, current_name, orig_name in rows:
        if not os.path.exists(current_path):
            print(f"File not found: {current_path}")
            continue
            
        dirpath = os.path.dirname(current_path)
        orig_path = os.path.join(dirpath, orig_name)
        try:
            os.rename(current_path, orig_path)
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("""
                UPDATE paper_analysis 
                SET filepath = ?, filename = ?, status = 'ready', applied_at = NULL, updated_at = ?
                WHERE filepath = ?
            """, (orig_path, orig_name, now_str, current_path))
            conn.commit()
            reverted_count += 1
            print(f"Reverted: {current_name} -> {orig_name}")
        except Exception as e:
            print(f"Error reverting {current_name}: {e}")
            
    print(f"\n[Rollback Finished] Reverted: {reverted_count} files.")
    export_reports(conn, os.path.dirname(db_path))

def main():
    parser = argparse.ArgumentParser(description="Rename paper PDFs with unrelated names (Plan C: Title_Author)")
    parser.add_argument("--root-dir", default="./papers", help="Target root directory")
    parser.add_argument("--db-path", default="./data/paper_rename_progress.sqlite", help="SQLite DB path")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Dry-run analysis only")
    parser.add_argument("--apply", action="store_true", default=False, help="Apply renames")
    parser.add_argument("--rollback", action="store_true", default=False, help="Rollback renames")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of files to process")
    
    args = parser.parse_args()
    
    if args.rollback:
        run_rollback(args.db_path)
    elif args.apply:
        run_apply(args.db_path)
    else:
        run_dry_run(args.root_dir, args.db_path, limit=args.limit)

if __name__ == "__main__":
    main()
