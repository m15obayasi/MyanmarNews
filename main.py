import os
import json
import logging
from datetime import datetime, timezone, timedelta
import html
import textwrap

import feedparser
import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

# =========================
# ログ設定
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="[{levelname}] {message}",
    style="{",
)

JST = timezone(timedelta(hours=9))
SEEN_FILE = "seen_articles.json"


# =========================
# Gemini クライアント関連
# =========================
def resolve_model_name() -> str:
    """環境変数 GEMINI_MODEL を優先しつつ、
    古い '-001' 形式なら自動で補正する。
    """
    env_model = os.getenv("GEMINI_MODEL")
    if env_model:
        # 旧SDK時代の "-001" 付きモデル名をいい感じに補正
        if env_model.endswith("-001"):
            normalized = env_model[:-4]
            logging.warning(
                "GEMINI_MODEL='%s' は旧形式の可能性があります。'%s' を代わりに使用します。",
                env_model,
                normalized,
            )
            return normalized
        return env_model

    # デフォルト（現行安定版）
    return "gemini-2.5-flash"


def create_gemini_client() -> tuple[genai.Client, str]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY が環境変数に設定されていません。")

    model = resolve_model_name()
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(api_version="v1"),
    )
    logging.info("Using Gemini model: %s", model)
    return client, model


# =========================
# RSS 関連
# =========================
RSS_SOURCES = [
    {
        "name": "The Irrawaddy (English)",
        "url": "https://www.irrawaddy.com/feed",
        "language": "en",
    },
    {
        "name": "Myanmar Now (English)",
        "url": "https://myanmar-now.org/en/feed/",
        "language": "en",
    },
    {
        "name": "Frontier Myanmar (English)",
        "url": "https://www.frontiermyanmar.net/en/feed/",
        "language": "en",
    },
]


def load_seen_articles() -> set[str]:
    if not os.path.exists(SEEN_FILE):
        logging.info("seen_articles.json not found. Creating new empty file.")
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(data)
        return set()
    except Exception as e:
        logging.warning("Failed to load %s: %s", SEEN_FILE, e)
        return set()


def save_seen_articles(seen_ids: set[str]) -> None:
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(seen_ids), f, ensure_ascii=False, indent=2)
        logging.info("Saved %d seen article IDs to %s", len(seen_ids), SEEN_FILE)
    except Exception as e:
        logging.error("Failed to save %s: %s", SEEN_FILE, e)


def pick_new_article(seen_ids: set[str]):
    """RSS を順番に見て、まだ投稿していない記事を1本だけ選ぶ。"""
    for source in RSS_SOURCES:
        name = source["name"]
        url = source["url"]
        logging.info("Checking RSS source: %s (%s)", name, url)

        feed = feedparser.parse(url)
        entries = feed.entries or []
        logging.info("RSS fetched: %s / entries = %d", url, len(entries))

        for entry in entries:
            article_id = entry.get("id") or entry.get("link")
            if not article_id:
                continue
            if article_id in seen_ids:
                continue

            logging.info(
                "Selected new article from %s: %s",
                name,
                entry.get("title", "No title"),
            )
            return source, entry, article_id

    logging.warning("No new article found in any RSS feeds.")
    return None, None, None


# =========================
# 記事本文スクレイピング（取れなければRSS要約だけでOK）
# =========================
def fetch_article_full_text(url: str) -> str | None:
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logging.warning("Failed to fetch article content: %s", e)
        return None

    try:
        soup = BeautifulSoup(resp.text, "lxml")

        # <article>タグ優先
        article_tag = soup.find("article")
        if article_tag:
            texts = [p.get_text(strip=True) for p in article_tag.find_all("p")]
        else:
            # フォールバック: body 内の <p> をざっくり
            texts = [p.get_text(strip=True) for p in soup.find_all("p")]

        full_text = "\n".join(t for t in texts if t)
        if not full_text.strip():
            return None
        return full_text
    except Exception as e:
        logging.warning("Failed to parse article HTML: %s", e)
        return None


# =========================
# Gemini 用プロンプト生成
# =========================
def build_gemini_prompt(source, entry, full_text: str | None) -> str:
    title_en = entry.get("title", "")
    summary_en = entry.get("summary", "") or entry.get("description", "")
    link = entry.get("link", "")
    source_name = source["name"]

    base_info = textwrap.dedent(
        f"""
        You are a professional Japanese news writer who is very familiar with Myanmar politics and society.
        Your task is to write a single Japanese blog article based on the following English news.

        [Source]
        - Media: {source_name}
        - Original title: {title_en}
        - URL: {link}

        [RSS summary in English]
        {summary_en}
        """
    ).strip()

    if full_text:
        base_info += "\n\n[Full article text in English]\n" + full_text

    jp_instructions = textwrap.dedent(
        """
        # Output requirements (in Japanese)

        - 日本語で 1500〜2500 文字程度のニュース記事を書いてください。
        - 単なる直訳ではなく、日本人読者向けに読みやすい自然な文章にしてください。
        - 重要な背景情報（ミャンマー政治・軍事政権・民族武装勢力など）があれば簡潔に補足してください。
        - 見出し（タイトル）、リード文（1〜3文）、本文の順で構成してください。
        - 語尾や文体は「です・ます調」で統一してください。
        - 憶測や感情的な表現は避け、事実ベースで書いてください。
        - 陰謀論・誇張表現は絶対に含めないでください。
        - 日本語タイトルの下に、元記事の英語タイトルとメディア名を1行で添えてください。
        - マークダウン形式で出力してください（# 見出し、## 小見出し など）。
        """
    ).strip()

    prompt = base_info + "\n\n" + jp_instructions
    return prompt


def generate_article_with_gemini(client: genai.Client, model: str, source, entry, full_text: str | None) -> str | None:
    prompt = build_gemini_prompt(source, entry, full_text)
    logging.info("Generating Japanese article with Gemini...")

    try:
        resp = client.models.generate_content(
            model=model,
            contents=[prompt],
            config=types.GenerateContentConfig(
                temperature=0.7,
                max_output_tokens=2048,
            ),
        )
        text = resp.text if hasattr(resp, "text") else None
        if not text or not text.strip():
            logging.error("Gemini returned empty response.")
            return None
        return text.strip()
    except Exception as e:
        logging.error("Gemini article generation failed: %s", e)
        return None


# =========================
# はてなブログ投稿
# =========================
def escape_xml(text: str) -> str:
    return html.escape(text, quote=False)


def post_to_hatena(title: str, body_markdown: str, categories=None, draft: bool = False) -> None:
    hatena_id = os.getenv("HATENA_ID")
    api_key = os.getenv("HATENA_API_KEY")
    blog_id = os.getenv("HATENA_BLOG_ID")

    if not hatena_id or not api_key or not blog_id:
        raise RuntimeError("HATENA_ID / HATENA_API_KEY / HATENA_BLOG_ID が設定されていません。")

    endpoint = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    if categories is None:
        categories = ["ミャンマー情勢", "ニュース"]

    updated = datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S%z")

    category_xml = ""
    for cat in categories:
        category_xml += f'  <category term="{escape_xml(cat)}" />\n'

    xml_body = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:app="http://www.w3.org/2007/app">
  <title>{escape_xml(title)}</title>
  <author><name>{escape_xml(hatena_id)}</name></author>
  <content type="text/x-markdown">
{escape_xml(body_markdown)}
  </content>
  <updated>{updated}</updated>
{category_xml}  <app:control>
    <app:draft>{"yes" if draft else "no"}</app:draft>
  </app:control>
</entry>
"""

    headers = {
        "Content-Type": "application/xml",
    }

    logging.info("Posting article to Hatena Blog...")
    resp = requests.post(
        endpoint,
        headers=headers,
        data=xml_body.encode("utf-8"),
        auth=(hatena_id, api_key),
        timeout=30,
    )

    if resp.status_code not in (200, 201, 202):
        logging.error("Hatena post failed: %s %s\nResponse: %s", resp.status_code, resp.reason, resp.text)
        resp.raise_for_status()

    logging.info("Hatena post success. Status: %s", resp.status_code)


# =========================
# メイン処理
# =========================
def main():
    print("==== Myanmar News Auto Poster (B: RSS翻訳＋要約モード) ====")

    client, model = create_gemini_client()

    # 既に投稿した記事一覧をロード
    seen_ids = load_seen_articles()

    # 新しい記事を1本選ぶ
    source, entry, article_id = pick_new_article(seen_ids)
    if not entry:
        logging.error("No new article found. Exit without posting.")
        return

    # 記事本文スクレイピング（403 等で失敗したら None）
    link = entry.get("link", "")
    full_text = fetch_article_full_text(link) if link else None

    # Gemini で日本語記事生成
    article_md = generate_article_with_gemini(client, model, source, entry, full_text)
    if not article_md:
        logging.error("Gemini failed to generate article. Exit without posting.")
        return

    # タイトルはマークダウンの先頭行 (# ...) から取る or フィードタイトル
    first_line = article_md.strip().splitlines()[0]
    if first_line.startswith("#"):
        title = first_line.lstrip("#").strip()
    else:
        title = entry.get("title", "ミャンマー情勢ニュース")

    # はてなブログへ投稿
    try:
        post_to_hatena(title, article_md, categories=["ミャンマー", "ニュース"], draft=False)
    except Exception as e:
        logging.error("Failed to post to Hatena: %s", e)
        return

    # 成功したら seen_articles に追加して保存
    seen_ids.add(article_id)
    save_seen_articles(seen_ids)


if __name__ == "__main__":
    main()
