#!/usr/bin/env python3
"""
rename_magazines.py
雑誌PDFの表紙画像をローカルVisionモデル（Gemma 4）で解析し、
安全に {特集タイトル-雑誌名}.pdf へリネームするバッチ処理スクリプト。
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

PROMPT_TEMPLATE = """この画像はPDFファイルから抽出されたページ画像です。
雑誌の表紙であるか判定し、表紙である場合は雑誌名や特集タイトル等を抽出してください。

【判定基準】
1. is_cover: 雑誌の「表紙（フロントカバー）」であれば true、本文や広告単体、目次単体などの場合は false。
2. magazine_name: 雑誌の正式名称（例: JOHNS, MB ENT, 兵庫県耳鼻咽喉科医会, 耳鼻臨床 など）。
3. issue_info: 年月や巻号情報（例: 2023年5月号, Vol.39 No.5, No.89 など）。
4. main_feature_title: 表紙に最も大きく書かれているメイン特集タイトルや主要記事題目。
5. has_mixed_magazines: 表紙や記載内容から、2つ以上の異なる雑誌が1つのPDFに混ざっている疑いがある場合は true。
6. suggested_title_and_magazine: "{main_feature_title}-{magazine_name}" の形式。
   ※可能であれば号情報も付加: 例 "手術をしない 音声・構音・言語の治療-JOHNS(2023年5月号)"

必ず以下のキーを持つJSONオブジェクトのみを出力してください。Markdown等の装飾は不要です。
{
  "is_cover": trueまたはfalse,
  "magazine_name": "雑誌名またはnull",
  "issue_info": "号情報またはnull",
  "main_feature_title": "特集タイトルまたはnull",
  "has_mixed_magazines": trueまたはfalse,
  "suggested_title_and_magazine": "タイトル-雑誌名"
}
"""

def sanitize_filename(name: str) -> str:
    """Linuxおよび一般的なファイルシステムで安全なファイル名に変換"""
    if not name:
        return ""
    # 禁止文字・危険な文字の置換: / \0 : * ? " < > | \n \r \t
    name = re.sub(r'[\/\\:\*\?"<>\|\n\r\t]', '_', name)
    # 連続する空白・アンダースコアの整理
    name = re.sub(r'\s+', ' ', name)
    name = re.sub(r'_+', '_', name)
    name = name.strip(' ._')
    # 長すぎるファイル名の切り詰め (最大180文字程度)
    if len(name.encode('utf-8')) > 200:
        name = name[:100]
    return name

def init_db(db_path: str):
    """進捗管理用SQLiteデータベースの初期化"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS file_analysis (
            filepath TEXT PRIMARY KEY,
            filename TEXT,
            status TEXT,  -- 'ready', 'mixed_magazines', 'no_cover_found', 'error', 'applied'
            cover_page INTEGER,
            magazine_name TEXT,
            issue_info TEXT,
            main_feature_title TEXT,
            has_mixed_magazines INTEGER,
            original_filename TEXT,
            target_filename TEXT,
            applied_at TEXT,
            error_message TEXT,
            raw_response TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    return conn

def render_page_to_base64(doc: pymupdf.Document, page_num: int, dpi: int = 140) -> str:
    """指定ページをPNG画像としてレンダリングし、Base64文字列で返す"""
    page = doc[page_num]
    pix = page.get_pixmap(dpi=dpi)
    img_bytes = pix.tobytes("png")
    return base64.b64encode(img_bytes).decode("utf-8")

def query_vision_model(base64_image: str, max_retries: int = 2) -> dict:
    """Gemma 4 Visionモデルに画像を送信してJSONレスポンスを取得"""
    payload = {
        "model": MODEL_NAME,
        "messages": [{
            "role": "user",
            "content": PROMPT_TEMPLATE,
            "images": [base64_image]
        }],
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.1
        }
    }
    
    for attempt in range(max_retries):
        try:
            res = requests.post(OLLAMA_API_URL, json=payload, timeout=60)
            if res.status_code == 200:
                content = res.json().get("message", {}).get("content", "{}")
                return json.loads(content)
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            time.sleep(1)
    return {}

def analyze_pdf(filepath: str, max_pages_to_check: int = 4) -> dict:
    """PDFの先頭から最大4ページまでを探索して表紙・メタデータを抽出"""
    try:
        doc = pymupdf.open(filepath)
    except Exception as e:
        return {
            "status": "error",
            "error_message": f"Failed to open PDF: {str(e)}"
        }
    
    total_pages = len(doc)
    pages_to_check = min(total_pages, max_pages_to_check)
    
    for page_idx in range(pages_to_check):
        try:
            b64_img = render_page_to_base64(doc, page_idx)
            result = query_vision_model(b64_img)
            
            is_cover = result.get("is_cover", False)
            if is_cover:
                has_mixed = result.get("has_mixed_magazines", False)
                mag_name = result.get("magazine_name") or ""
                issue = result.get("issue_info") or ""
                feature = result.get("main_feature_title") or ""
                suggested = result.get("suggested_title_and_magazine") or ""
                
                # 新ファイル名の組み立て
                def clean_str(s):
                    if not s or str(s).lower() in ("null", "none"):
                        return ""
                    return str(s).strip()

                feature = clean_str(feature)
                mag_name = clean_str(mag_name)
                issue = clean_str(issue)
                
                # "null-" などの混入を掃除
                if suggested:
                    suggested = re.sub(r'^(null|none)[-_ ]*', '', suggested, flags=re.IGNORECASE)
                    suggested = re.sub(r'[-_ ]*(null|none)$', '', suggested, flags=re.IGNORECASE)
                    suggested = clean_str(suggested)

                if feature and mag_name:
                    if issue:
                        base_name = f"{feature}-{mag_name}({issue})"
                    else:
                        base_name = f"{feature}-{mag_name}"
                elif feature:
                    base_name = f"{feature}({issue})" if issue else feature
                elif mag_name:
                    base_name = f"{mag_name}({issue})" if issue else mag_name
                elif suggested:
                    base_name = suggested
                else:
                    base_name = ""
                
                clean_name = sanitize_filename(base_name)
                target_filename = f"{clean_name}.pdf" if clean_name else ""
                
                status = "mixed_magazines" if has_mixed else ("ready" if target_filename else "no_title_extracted")
                
                return {
                    "status": status,
                    "cover_page": page_idx + 1,
                    "magazine_name": mag_name,
                    "issue_info": issue,
                    "main_feature_title": feature,
                    "has_mixed_magazines": 1 if has_mixed else 0,
                    "target_filename": target_filename,
                    "raw_response": json.dumps(result, ensure_ascii=False)
                }
        except Exception as e:
            continue
            
    return {
        "status": "no_cover_found",
        "error_message": f"No cover detected within first {pages_to_check} pages"
    }

def export_reports(conn: sqlite3.Connection, output_dir: str):
    """現在の全解析結果をCSVおよびJSONでエクスポート"""
    cur = conn.cursor()
    cur.execute("SELECT * FROM file_analysis ORDER BY filepath ASC")
    rows = cur.fetchall()
    cols = [desc[0] for desc in cur.description]
    
    csv_path = os.path.join(output_dir, "magazine_rename_report.csv")
    json_path = os.path.join(output_dir, "magazine_rename_report.json")
    
    data = []
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        for r in rows:
            writer.writerow(r)
            data.append(dict(zip(cols, r)))
            
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        
    print(f"[Export] Report saved to:\n  - CSV:  {csv_path}\n  - JSON: {json_path}")

def run_dry_run(target_dir: str, db_path: str, limit: int = None):
    """全PDFを走査し、Visionモデルで解析してDBに保存（リネームはしない）"""
    conn = init_db(db_path)
    cur = conn.cursor()
    
    files = sorted(glob.glob(os.path.join(target_dir, "*.pdf")))
    total_files = len(files)
    print(f"=== [Dry-Run] Target Directory: {target_dir} ({total_files} PDF files) ===")
    
    processed_count = 0
    skipped_count = 0
    
    for idx, filepath in enumerate(files, 1):
        if limit and processed_count >= limit:
            print(f"[Limit reached: {limit} files]")
            break
            
        filename = os.path.basename(filepath)
        
        # 既にDBに正常完了データがあるかチェック
        cur.execute("SELECT status, target_filename FROM file_analysis WHERE filepath = ?", (filepath,))
        row = cur.fetchone()
        if row and row[0] in ('ready', 'mixed_magazines', 'applied'):
            skipped_count += 1
            continue
            
        print(f"[{idx}/{total_files}] Analyzing: {filename} ... ", end="", flush=True)
        t0 = time.time()
        res = analyze_pdf(filepath)
        elapsed = time.time() - t0
        
        status = res.get("status", "error")
        target_name = res.get("target_filename", "")
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        
        cur.execute("""
            INSERT OR REPLACE INTO file_analysis (
                filepath, filename, status, cover_page, magazine_name, issue_info,
                main_feature_title, has_mixed_magazines, original_filename, target_filename,
                applied_at, error_message, raw_response, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            filepath,
            filename,
            status,
            res.get("cover_page"),
            res.get("magazine_name"),
            res.get("issue_info"),
            res.get("main_feature_title"),
            res.get("has_mixed_magazines", 0),
            filename,
            target_name,
            None,
            res.get("error_message"),
            res.get("raw_response"),
            now_str
        ))
        conn.commit()
        processed_count += 1
        
        if status == "ready":
            print(f"OK ({elapsed:.1f}s) -> '{target_name}'")
        elif status == "mixed_magazines":
            print(f"MIXED/SKIP ({elapsed:.1f}s) (2誌混在の疑い)")
        else:
            print(f"{status.upper()} ({elapsed:.1f}s): {res.get('error_message', '')}")
            
    print(f"\n[Summary] Processed: {processed_count}, Skipped (Already Analyzed): {skipped_count}")
    export_reports(conn, os.path.dirname(db_path))

def run_apply(target_dir: str, db_path: str):
    """解析結果に基づき、安全にファイル名を変更"""
    conn = init_db(db_path)
    cur = conn.cursor()
    
    cur.execute("SELECT filepath, filename, target_filename FROM file_analysis WHERE status = 'ready'")
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
            
        target_path = os.path.join(target_dir, target_name)
        
        # 同名衝突回避（既に存在する場合、_1, _2 等を付加）
        if os.path.exists(target_path) and target_path != filepath:
            base, ext = os.path.splitext(target_name)
            counter = 1
            while os.path.exists(os.path.join(target_dir, f"{base}_{counter}{ext}")):
                counter += 1
            target_name = f"{base}_{counter}{ext}"
            target_path = os.path.join(target_dir, target_name)
            collision_count += 1
            
        try:
            os.rename(filepath, target_path)
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("""
                UPDATE file_analysis 
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

def run_rollback(target_dir: str, db_path: str):
    """リネームされたファイルを元のファイル名に戻す"""
    conn = init_db(db_path)
    cur = conn.cursor()
    
    cur.execute("SELECT filepath, filename, original_filename FROM file_analysis WHERE status = 'applied'")
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
            
        orig_path = os.path.join(target_dir, orig_name)
        try:
            os.rename(current_path, orig_path)
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("""
                UPDATE file_analysis 
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
    parser = argparse.ArgumentParser(description="Rename magazine PDFs using Gemma 4 Vision")
    parser.add_argument("--target-dir", default="./magazines", help="Target directory containing PDFs")
    parser.add_argument("--db-path", default="./data/rename_progress.sqlite", help="SQLite database for progress tracking")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Run analysis and report without modifying files")
    parser.add_argument("--apply", action="store_true", default=False, help="Apply renames for all 'ready' files")
    parser.add_argument("--rollback", action="store_true", default=False, help="Rollback all applied renames")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of files to process")
    
    args = parser.parse_args()
    
    if args.rollback:
        run_rollback(args.target_dir, args.db_path)
    elif args.apply:
        run_apply(args.target_dir, args.db_path)
    else:
        # デフォルトは安全のため dry-run
        run_dry_run(args.target_dir, args.db_path, limit=args.limit)

if __name__ == "__main__":
    main()
