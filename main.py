import os
import json
import html
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

import requests
import feedparser
from bs4 import BeautifulSoup

from google import genai
from google.genai.types import HttpOptions

import markdown as md
from xml.etree.ElementTree import Element, SubElement, tostring

# ============================================================
# 設定
# ============================================================

SEEN_FILE = "seen_articles.json"

RSS_SOURCES = [
    {
        "name": "The Irrawaddy (English)",
        "url": "https://www.irrawaddy.com/feed",
    },
    {
        "name": "Myanmar Now (English)",
        "url": "https://myanmar-now.org/en/feed/",
    },
    {
        "name": "Frontier Myanmar (English)",
        "url": "https://www.frontiermyanmar.net/en/feed/",
    },
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ============================================================
# ちょい便利なログ
# ============================================================

def info(msg: str) -> None:
    print(f"[INFO] {msg}")


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def error(msg: str) -> None:
    print(f"[ERROR] {msg}")


# ============================================================
# seen_articles.json の管理
# ============================================================

def load_seen_articles() -> set:
    """既読記事IDのセットを読み込む。なければ空ファイルを作る。"""
    if not os.path.exists(SEEN_FILE):
        info("seen_articles.json not found. Creating new empty file.")
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        return set()

    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        seen = set(data)
        info(f"Loaded {len(seen)} seen article IDs.")
        return seen
    except Exception as e:
        error(f"Failed to load {SEEN_FILE}: {e}")
        return set()


def save_seen_articles(seen_ids: set) -> None:
    """既読記事IDセットを JSON ファイルに保存する。"""
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(seen_ids)), f, ensure_ascii=False, indent=2)
        info(f"Saved {len(seen_ids)} seen article IDs.")
    except Exception as e:
        error(f"Failed to save {SEEN_FILE}: {e}")


# ============================================================
# RSS から新しい記事を 1 本だけ選ぶ
# ============================================================

def choose_new_article(seen_ids: set) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str]]:
    """
    RSS_SOURCES を上から順に見て、未読の記事を 1 件だけ返す。
    戻り値: (entry, source, article_id) / 見つからなければ (None, None, None)
    """
    for src in RSS_SOURCES:
        name = src["name"]
        url = src["url"]
        info(f"Checking RSS source: {name} ({url})")

        feed = feedparser.parse(url)
        entries = feed.entries or []
        info(f"RSS fetched: {url} / entries = {len(entries)}")

        for entry in entries:
            article_id = entry.get("id") or entry.get("link")
            if not article_id:
                continue
            if article_id in seen_ids:
                continue

            title = entry.get("title", "(no title)")
            info(f"Selected new article from {name}: {title}")
            return entry, src, article_id

    info("No new article found in any RSS source.")
    return None, None, None


# ============================================================
# 元記事本文の取得
# ============================================================

def fetch_article_content(url: str) -> Optional[str]:
    """元記事 URL から本文テキストを取ってくる（失敗したら None）。"""
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        warn(f"Failed to fetch article content: {e}")
        return None

    html_text = resp.text
    soup = BeautifulSoup(html_text, "lxml")

    # よくある記事コンテナを順に探す
    candidates = [
        {"name": "article"},
        {"name": "div", "attrs": {"class": lambda c: c and "article" in c}},
        {"name": "div", "attrs": {"class": lambda c: c and "post-content" in c}},
        {"name": "div", "attrs": {"id": "content"}},
    ]

    for cond in candidates:
        node = soup.find(cond.get("name"), attrs=cond.get("attrs"))
        if node:
            text = node.get_text(separator="\n", strip=True)
            if text:
                return text

    # フォールバック: 全文から script/style を除いてテキスト抽出
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return text or None


# ============================================================
# Gemini 用プロンプト生成
# ============================================================

def build_gemini_prompt(entry: Dict[str, Any], article_text: Optional[str], source_name: str) -> str:
    title = entry.get("title", "")
    link = entry.get("link", "")
    published = entry.get("published", "") or entry.get("updated", "")

    base_text_parts: List[str] = []

    if article_text:
        base_text_parts.append(article_text)
    else:
        # 本文取得に失敗した場合は RSS の要約・説明などで代用
        summary = entry.get("summary", "") or entry.get("description", "")
        base_text_parts.append(summary or "")

    base_text = "\n\n".join(part for part in base_text_parts if part)

    prompt = f"""
You are a professional Japanese journalist writing for a blog about Myanmar.

Write a single Japanese blog article based on the following English news article
about Myanmar. Your tasks:

1. Summarize the key points accurately.
2. Translate into natural Japanese.
3. Add context that helps Japanese readers understand Myanmar's situation.
4. Do NOT invent facts.
5. Make the tone neutral but easy to read.
6. Do not include the original English text in the output.
7. Output in Markdown.
8. First line should be the Japanese title (you may add a short, catchy title).
9. After the title, insert a blank line, then the body.
10. At the end, add a short source note like:
「出典: {source_name}（英語原文）」

Original article metadata:
- Source: {source_name}
- Title: {title}
- URL: {link}
- Published: {published}

Original article content (English or partial):

\"\"\"{base_text}\"\"\"
    """.strip()

    return prompt


# ============================================================
# Gemini で記事生成
# ============================================================

def create_gemini_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")

    # ★ここがポイント：API バージョンを v1 に固定
    client = genai.Client(
        api_key=api_key,
        http_options=HttpOptions(api_version="v1"),
    )
    return client


def generate_article_with_gemini(client: genai.Client, model_name: str, prompt: str) -> Optional[str]:
    info("Generating Japanese article with Gemini...")
    try:
        resp = client.models.generate_content(
            model=model_name,
            contents=[prompt],
        )
    except Exception as e:
        # google-genai の例外メッセージをそのまま出す
        error(f"Gemini article generation failed: {e}")
        return None

    text = getattr(resp, "text", None)
    if not text:
        error("Gemini returned empty response text.")
        return None

    return text


# ============================================================
# Gemini 出力のパース（1行目をタイトル扱い）
# ============================================================

def parse_gemini_output_to_title_and_body(text: str) -> Tuple[str, str]:
    lines = [ln.rstrip() for ln in text.splitlines()]
    # 最初の非空行をタイトル候補に
    first_idx = None
    for i, ln in enumerate(lines):
        if ln.strip():
            first_idx = i
            break

    if first_idx is None:
        # 何もなかったら丸ごと本文扱い
        return "ミャンマー情勢ニュースダイジェスト", text

    title_line = lines[first_idx].strip()
    # 先頭の # を削る
    if title_line.startswith("#"):
        title_line = title_line.lstrip("#").strip()
    if not title_line:
        title_line = "ミャンマー情勢ニュースダイジェスト"

    body_lines = lines[first_idx + 1 :]
    # 先頭の空行は削る
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)

    body = "\n".join(body_lines).strip()
    if not body:
        body = "（本文生成に失敗しました）"

    return title_line, body


# ============================================================
# Hatena Blog への投稿
# ============================================================

def markdown_to_html(md_text: str) -> str:
    return md.markdown(md_text, output_format="html5")


def post_to_hatena(title: str, body_markdown: str) -> Optional[str]:
    hatena_id = os.getenv("HATENA_ID")
    hatena_blog_id = os.getenv("HATENA_BLOG_ID")
    hatena_api_key = os.getenv("HATENA_API_KEY")

    if not hatena_id or not hatena_blog_id or not hatena_api_key:
        error("Hatena credentials are not fully set (HATENA_ID / HATENA_BLOG_ID / HATENA_API_KEY).")
        return None

    blog_url = f"https://blog.hatena.ne.jp/{hatena_id}/{hatena_blog_id}/atom/entry"

    content_html = markdown_to_html(body_markdown)

    # AtomPub エントリを XML 生成
    entry = Element("entry", {"xmlns": "http://www.w3.org/2005/Atom"})

    title_elem = SubElement(entry, "title")
    title_elem.text = title

    author_elem = SubElement(entry, "author")
    name_elem = SubElement(author_elem, "name")
    name_elem.text = hatena_id

    content_elem = SubElement(entry, "content", {"type": "html"})
    content_elem.text = content_html

    updated_elem = SubElement(entry, "updated")
    updated_elem.text = datetime.utcnow().isoformat() + "Z"

    # 必要ならカテゴリを一個つけておく（任意）
    cat_elem = SubElement(
        entry,
        "category",
        {"term": "Myanmar", "scheme": "http://www.hatena.ne.jp/categories"},
    )

    xml_bytes = tostring(entry, encoding="utf-8")

    headers = {
        "Content-Type": "application/atom+xml",
    }

    try:
        resp = requests.post(
            blog_url,
            headers=headers,
            data=xml_bytes,
            auth=(hatena_id, hatena_api_key),
            timeout=20,
        )
    except Exception as e:
        error(f"Failed to POST to Hatena Blog: {e}")
        return None

    if resp.status_code == 201:
        info("Hatena Blog post succeeded (status 201 Created).")
        # Location ヘッダに記事URLが入っている場合が多い
        post_url = resp.headers.get("Location") or ""
        if post_url:
            info(f"Posted entry URL: {post_url}")
        return post_url or None
    else:
        error(f"Hatena Blog post failed: status={resp.status_code}, body={resp.text[:500]}")
        return None


# ============================================================
# メイン処理
# ============================================================

def main() -> None:
    print("==== Myanmar News Auto Poster (B: RSS翻訳＋要約モード) ====")

    # Gemini 設定
    model_name = os.getenv("GEMINI_MODEL", "gemini-1.5-flash-001")
    info(f"Using Gemini model: {model_name}")

    try:
        client = create_gemini_client()
    except Exception as e:
        error(f"Failed to create Gemini client: {e}")
        return

    # 既読記事ロード（なければ空ファイルを作る）
    seen_ids = load_seen_articles()

    # 新しい記事を 1 件だけ選ぶ
    entry, source, article_id = choose_new_article(seen_ids)
    if not entry or not source or not article_id:
        info("No new article to post. Exit.")
        return

    # 元記事本文を試しに取得（403などなら None）
    link = entry.get("link", "")
    article_text = None
    if link:
        article_text = fetch_article_content(link)

    # Gemini 用プロンプト作成
    prompt = build_gemini_prompt(entry, article_text, source_name=source["name"])

    # Gemini で記事生成
    gemini_output = generate_article_with_gemini(client, model_name, prompt)
    if not gemini_output:
        error("Gemini failed to generate article. Exit without posting.")
        return

    # タイトル & 本文に分割
    title_ja, body_md = parse_gemini_output_to_title_and_body(gemini_output)

    # はてなブログに投稿
    post_url = post_to_hatena(title_ja, body_md)
    if not post_url:
        error("Failed to post to Hatena Blog. Exit without marking article as seen.")
        return

    # 正常終了したので既読扱いにして保存
    seen_ids.add(article_id)
    save_seen_articles(seen_ids)

    info("All done.")


if __name__ == "__main__":
    main()
