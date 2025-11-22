import os
import json
import logging
import traceback
import html
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set, Tuple

import requests
import feedparser
from bs4 import BeautifulSoup
import markdown


# ===============================
# ログ設定
# ===============================
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
)

# ===============================
# RSS ソース定義
# ===============================
RSS_SOURCES = [
    {
        "name": "The Irrawaddy (English)",
        "url": "https://www.irrawaddy.com/feed",
        "lang": "en",
    },
    # 必要ならここに他の RSS 追加
]

SEEN_FILE = "seen_articles.json"


# ===============================
# ユーティリティ
# ===============================
def load_seen_ids() -> Set[str]:
    """過去に投稿した記事 ID を読み込む"""
    if not os.path.exists(SEEN_FILE):
        logging.info("[INFO] seen_articles.json not found. Creating new empty file.")
        save_seen_ids(set())
        return set()

    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(data)
        elif isinstance(data, dict) and "ids" in data:
            return set(data["ids"])
        else:
            logging.warning("[WARN] seen_articles.json format unknown, resetting.")
            return set()
    except Exception as e:
        logging.warning(f"[WARN] Failed to load seen_articles.json: {e}")
        return set()


def save_seen_ids(ids: Set[str]) -> None:
    """投稿済み記事 ID を保存する"""
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(ids)), f, ensure_ascii=False, indent=2)
        logging.info("[INFO] seen_articles.json updated.")
    except Exception as e:
        logging.error(f"[ERROR] Failed to save seen_articles.json: {e}")


def fetch_rss_entries(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    """RSS を取得して entries を返す"""
    url = source["url"]
    name = source["name"]
    logging.info(f"[INFO] Checking RSS source: {name} ({url})")

    feed = feedparser.parse(url)
    entries = getattr(feed, "entries", []) or []
    logging.info(f"[INFO] RSS fetched: {url} / entries = {len(entries)}")
    return entries


def choose_new_entry(
    entries: List[Dict[str, Any]],
    seen_ids: Set[str],
) -> Optional[Tuple[Dict[str, Any], str]]:
    """まだ投稿していない記事を 1 本選ぶ"""

    for e in entries:
        entry_id = getattr(e, "id", None) or getattr(e, "link", None)
        if not entry_id:
            # ID が無い時はタイトル＋リンクとかで擬似 ID を作る
            entry_id = (getattr(e, "title", "") + "|" + getattr(e, "link", "")).strip()

        if entry_id in seen_ids:
            continue
        return e, entry_id

    return None


def fetch_article_html(url: str) -> Optional[str]:
    """記事本体の HTML を取得（403 などは警告だけ出して None を返す）"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.HTTPError as e:
        logging.warning(f"[WARNING] Failed to fetch article content: {e}")
        return None
    except Exception as e:
        logging.warning(f"[WARNING] Error fetching article content: {e}")
        return None


def html_to_text(html_content: str) -> str:
    """HTML からテキストをざっくり抽出"""
    soup = BeautifulSoup(html_content, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    # 行を整形
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


# ===============================
# Gemini REST (v1beta) 呼び出し
# ===============================
def get_gemini_model_name() -> str:
    """環境変数からモデル名を取得（指定が無ければデフォルトを使う）"""
    env_model = os.getenv("GEMINI_MODEL")
    if env_model:
        logging.info(f"[INFO] Using Gemini model from env: {env_model}")
        return env_model

    # デフォルト（コンソールに表示される model name に合わせて適宜変更）
    default_model = "gemini-2.5-flash"
    logging.info(f"[INFO] GEMINI_MODEL not set. Using default: {default_model}")
    return default_model


def call_gemini_generate_content(prompt: str) -> str:
    """
    Gemini API (v1beta) を REST で叩いてテキストを返す。
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in environment variables.")

    model = get_gemini_model_name()

    base_url = "https://generativelanguage.googleapis.com/v1beta"
    url = f"{base_url}/models/{model}:generateContent"

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }

    logging.info(f"[INFO] Calling Gemini REST API (model={model}) ...")

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    try:
        resp.raise_for_status()
    except Exception:
        logging.error(f"[ERROR] Gemini HTTP error: {resp.status_code} {resp.text}")
        raise

    data = resp.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini response has no candidates.")

    content = candidates[0].get("content", {})
    parts = content.get("parts", [])
    if not parts:
        raise RuntimeError("Gemini response has no parts in content.")

    text = "".join(part.get("text", "") for part in parts)
    if not text.strip():
        raise RuntimeError("Gemini response text is empty.")

    return text.strip()


def build_prompt_for_article(
    source_name: str,
    entry: Any,
    article_text: str,
) -> str:
    """英語記事 → 日本語ブログ記事を書くよう Gemini にお願いするプロンプトを生成"""

    title = getattr(entry, "title", "")
    link = getattr(entry, "link", "")
    summary = getattr(entry, "summary", "")

    # 記事本文が取れなかった場合は summary を使う
    base_text = article_text.strip() or summary.strip() or title

    prompt = f"""
あなたはミャンマー情勢に詳しい日本語ブロガーです。
以下の英語ニュース記事の内容をもとに、
日本語でわかりやすいブログ記事を書いてください。

# 制約・トーン
- 読者は「ミャンマーのことはある程度知っているが、現地ニュースを英語で追うのは大変」という日本の一般人を想定してください。
- 難しい政治用語は、できるだけ日本語で補足しながら説明してください。
- 陰謀論や極端な主張は避け、事実ベース＋穏やかな意見にとどめてください。
- 文字数はだいたい 1200〜2000 文字程度。
- 日本語のタイトルを 1 行目に書いてください。
- 2 行目以降は、以下のような構成で Markdown 形式で書いてください:
  - 導入：ニュースの概要を 2〜3 文で
  - 背景：なぜこの出来事が起きているのか
  - 今回のニュースのポイント：箇条書きでも可
  - ミャンマー市民や周辺国・国際社会への影響
  - ブロガーとしての簡単なコメント（主観）は最後に短く

# ニュースソース
- Source: {source_name}
- Original Title: {title}
- URL: {link}

# 英語記事本文（または要約）
{base_text}
""".strip()

    return prompt


def split_title_and_body_from_gemini(text: str) -> Tuple[str, str]:
    """
    Gemini の出力からタイトル＋本文をざっくり分離。
    - 1 行目をタイトル
    - 2 行目以降を本文として扱う
    """
    lines = text.splitlines()
    if not lines:
        return "ミャンマー情勢ニュース", text

    title = lines[0].strip().lstrip("#").strip()  # 先頭に # が付いていれば削る
    body = "\n".join(lines[1:]).strip()
    if not body:
        body = title
    return title, body


# ===============================
# はてなブログ投稿
# ===============================
def post_to_hatena(title: str, body_md: str, source_link: str) -> None:
    """
    はてなブログに記事を投稿する（AtomPub）。
    content は HTML として送る。
    """
    hatena_id = os.getenv("HATENA_ID")
    api_key = os.getenv("HATENA_API_KEY")
    blog_id = os.getenv("HATENA_BLOG_ID")

    if not (hatena_id and api_key and blog_id):
        raise RuntimeError("HATENA_ID / HATENA_API_KEY / HATENA_BLOG_ID が設定されていません。")

    endpoint = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    # Markdown → HTML
    body_html = markdown.markdown(body_md)

    # 元記事リンクを最後に付与
    if source_link:
        body_html += f'<hr><p>Source: <a href="{html.escape(source_link)}">{html.escape(source_link)}</a></p>'

    updated = datetime.now(timezone.utc).isoformat()

    entry_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:app="http://www.w3.org/2007/app">
  <title>{html.escape(title)}</title>
  <author><name>{html.escape(hatena_id)}</name></author>
  <content type="text/html">{html.escape(body_html)}</content>
  <updated>{updated}</updated>
  <app:control>
    <app:draft>no</app:draft>
  </app:control>
</entry>
""".strip()

    auth = (hatena_id, api_key)
    headers = {"Content-Type": "application/xml"}

    logging.info("[INFO] Posting article to Hatena Blog...")
    resp = requests.post(endpoint, data=entry_xml.encode("utf-8"), headers=headers, auth=auth, timeout=30)
    try:
        resp.raise_for_status()
    except Exception:
        logging.error(f"[ERROR] Hatena Blog post failed: {resp.status_code} {resp.text}")
        raise

    logging.info("[INFO] Hatena Blog post success.")


# ===============================
# メイン処理
# ===============================
def main() -> None:
    logging.info("==== Myanmar News Auto Poster (B: RSS翻訳＋要約モード) ====")

    seen_ids = load_seen_ids()

    selected_entry = None
    selected_entry_id = None
    selected_source = None

    # 1. RSS から未読記事を 1 本選ぶ
    for source in RSS_SOURCES:
        entries = fetch_rss_entries(source)
        result = choose_new_entry(entries, seen_ids)
        if result is None:
            continue
        entry, entry_id = result
        selected_entry = entry
        selected_entry_id = entry_id
        selected_source = source
        break

    if not selected_entry:
        logging.info("[INFO] No new articles found in all RSS sources. Exit.")
        return

    logging.info(
        f"[INFO] Selected new article from {selected_source['name']}: "
        f"{getattr(selected_entry, 'title', '')}"
    )

    # 2. 記事本文を取得（失敗しても summary ベースで進める）
    link = getattr(selected_entry, "link", "")
    article_text = ""
    if link:
        html_content = fetch_article_html(link)
        if html_content:
            article_text = html_to_text(html_content)

    # 3. Gemini に日本語記事を生成してもらう
    prompt = build_prompt_for_article(
        source_name=selected_source["name"],
        entry=selected_entry,
        article_text=article_text,
    )

    try:
        logging.info("[INFO] Generating Japanese article with Gemini (REST v1beta)...")
        gemini_output = call_gemini_generate_content(prompt)
    except Exception as e:
        logging.error(f"[ERROR] Gemini article generation failed: {e}")
        logging.error(traceback.format_exc())
        logging.error("[ERROR] Gemini failed to generate article. Exit without posting.")
        return

    title_ja, body_md = split_title_and_body_from_gemini(gemini_output)

    # 4. はてなブログに投稿
    try:
        post_to_hatena(title_ja, body_md, getattr(selected_entry, "link", ""))
    except Exception as e:
        logging.error(f"[ERROR] Failed to post to Hatena Blog: {e}")
        logging.error(traceback.format_exc())
        return

    # 5. 投稿済み ID を保存
    if selected_entry_id:
        seen_ids.add(selected_entry_id)
        save_seen_ids(seen_ids)


if __name__ == "__main__":
    main()
