import os
import json
import datetime
from xml.sax.saxutils import escape as xml_escape

import requests
import feedparser
from bs4 import BeautifulSoup
from markdown import markdown

from google import genai
from google.genai.types import HttpOptions


# ================== 設定 ==================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
HATENA_ID = os.getenv("HATENA_ID")
HATENA_API_KEY = os.getenv("HATENA_API_KEY")
HATENA_BLOG_ID = os.getenv("HATENA_BLOG_ID")

# 使用するGeminiモデル
GEMINI_MODEL = "gemini-1.5-flash"

# 既に処理した記事IDを保存するファイル
SEEN_FILE = "seen_articles.json"

# RSSを取得するニュースサイト（必要に応じてURLを調整してください）
RSS_SOURCES = [
    {
        "name": "The Irrawaddy (English)",
        "feed_url": "https://www.irrawaddy.com/feed",
        "language": "English",
    },
    {
        "name": "Myanmar Now (English)",
        "feed_url": "https://myanmar-now.org/en/feed/",
        "language": "English",
    },
    {
        "name": "Frontier Myanmar (English)",
        "feed_url": "https://frontiermyanmar.net/en/feed/",
        "language": "English",
    },
]

HTTP_HEADERS = {
    "User-Agent": "MyanmarNewsBot/0.1 (+https://www.yangon.tokyo/)"
}


# ================== 共通ユーティリティ ==================

def load_seen_articles() -> set:
    """これまでに記事化したエントリIDを読み込む"""
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(data)
        return set()
    except Exception as e:
        print(f"[WARN] failed to load {SEEN_FILE}: {e}")
        return set()


def save_seen_articles(seen_ids: set) -> None:
    """記事化済みIDを保存する"""
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(seen_ids)), f, ensure_ascii=False, indent=2)
        print(f"[INFO] saved {len(seen_ids)} seen article IDs.")
    except Exception as e:
        print(f"[ERROR] failed to save {SEEN_FILE}: {e}")


def fetch_rss_feed(feed_url: str):
    """RSSフィードを取得してfeedparserでパースする"""
    try:
        resp = requests.get(feed_url, headers=HTTP_HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ERROR] failed to fetch RSS: {feed_url} -> {e}")
        return None

    # bytesのまま渡すことで、feedparserにエンコーディング判定を任せる
    feed = feedparser.parse(resp.content)
    if getattr(feed, "bozo", False):
        print(f"[WARN] RSS parse issue for {feed_url}: {feed.bozo_exception}")
    print(f"[INFO] RSS fetched: {feed_url} / entries = {len(feed.entries)}")
    return feed


def extract_entry_body(entry) -> str:
    """RSSエントリから本文HTMLを抽出し、テキスト化したものを返す"""
    html = ""
    # WordPress系だと content[0].value に本⽂が入っていることが多い
    if hasattr(entry, "content") and entry.content:
        html = entry.content[0].value
    elif hasattr(entry, "summary"):
        html = entry.summary
    else:
        html = ""

    if not html:
        return ""

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    # スクリプト類は除去
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # pタグを優先してテキスト抽出
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    text = "\n".join(p for p in paragraphs if p)

    if not text:
        # 最悪の場合は全テキスト
        text = soup.get_text(" ", strip=True)

    # 長すぎる場合は安全のためカット（Gemini入力上限対策）
    max_len = 20000
    if len(text) > max_len:
        print(f"[INFO] article text too long ({len(text)} chars). Truncating to {max_len}.")
        text = text[:max_len]

    return text


def select_new_article(seen_ids: set):
    """
    RSS_SOURCESを上から順に見ていき、
    「まだ記事化していない最新エントリ」を1件だけ返す。
    """
    for source in RSS_SOURCES:
        name = source["name"]
        url = source["feed_url"]
        lang = source["language"]

        print(f"[INFO] Checking RSS source: {name} ({url})")
        feed = fetch_rss_feed(url)
        if not feed or not feed.entries:
            continue

        # フィードは通常、新しい順に並んでいる想定
        for entry in feed.entries:
            entry_id = getattr(entry, "id", "") or getattr(entry, "link", "") or getattr(entry, "title", "")
            link = getattr(entry, "link", "")
            title = getattr(entry, "title", "").strip()

            if not entry_id and not link and not title:
                continue

            unique_id = f"{name}|{entry_id or link or title}"

            if unique_id in seen_ids:
                continue

            body_text = extract_entry_body(entry)
            if not body_text:
                print(f"[WARN] entry has no body text, skipping: {title} ({link})")
                continue

            print(f"[INFO] Selected new article from {name}: {title}")
            return {
                "source_name": name,
                "source_lang": lang,
                "title": title,
                "link": link,
                "unique_id": unique_id,
                "body_text": body_text,
            }

    return None


# ================== Gemini関連 ==================

def create_gemini_client() -> genai.Client:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set.")
    client = genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=HttpOptions(api_version="v1"),
    )
    return client


def summarize_and_translate_with_gemini(client: genai.Client, article: dict) -> str:
    """
    元記事の本文をもとに、
    日本語のブログ記事（Markdown）を1本生成する。
    """
    source_name = article["source_name"]
    source_lang = article["source_lang"]
    title = article["title"]
    url = article["link"]
    body_text = article["body_text"]

    print(f"[INFO] Generating Japanese article with Gemini...")
    prompt = f"""
あなたはミャンマー情勢に詳しい日本人ジャーナリストです。
以下のニュース記事（主に {source_lang} ）を読み、日本語でブログ用の記事を1本作成してください。

# 出力条件

- 出力は **Markdown形式** とする
- 先頭行は記事タイトルで「# 」から始める（1つだけ）
- タイトルは日本語で、ミャンマー情勢が分かるように簡潔かつインパクトのあるものにする
- 続けて2〜4文のリード文（導入）を書く
- その後、少なくとも2〜4個の見出し「## 見出しタイトル」を使い、本文を構成する
- 全体の長さは日本語で概ね **1500〜2500文字** を目安とする
- 文体は「〜です・〜ます調」
- 日本の一般的なニュース読者を想定し、背景説明や地名・組織の補足を適宜入れる
- 事実関係は元記事に忠実に。ただし、事実が不明な点について勝手な推測や陰謀論は書かない
- 元記事の主張・トーンを尊重しつつも、「日本語で読みやすい自然な構成」に再構成してよい
- 記事の最後に「## 参照元」セクションを作り、以下の形式で元記事を1件だけ記載する
    - {source_name}（{source_lang}）: {url}

# 元記事のメタ情報

- サイト名: {source_name}
- 言語: {source_lang}
- 記事タイトル: {title}
- URL: {url}

# 元記事本文（そのままのテキスト）

{body_text}
""".strip()

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )

    if not hasattr(response, "text") or not response.text:
        raise RuntimeError("Gemini response has no text.")

    article_md = response.text.strip()
    print("[INFO] Gemini article generation completed.")
    return article_md


# ================== はてなブログ投稿 ==================

def build_hatena_entry_xml(title: str, content_html: str, categories=None, draft: bool = False) -> str:
    """はてなブログAtomPub用のXMLを組み立てる"""
    if categories is None:
        categories = []

    updated = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # ISO 8601 末尾をZにする
    if updated.endswith("+00:00"):
        updated = updated.replace("+00:00", "Z")

    categories_xml = ""
    for cat in categories:
        categories_xml += f'  <category term="{xml_escape(cat)}" />\n'

    draft_flag = "yes" if draft else "no"

    entry_xml = f'''<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:app="http://www.w3.org/2007/app">
  <title>{xml_escape(title)}</title>
  <author><name>{xml_escape(HATENA_ID or "")}</name></author>
  <content type="text/html">{xml_escape(content_html)}</content>
  <updated>{updated}</updated>
{categories_xml}  <app:control>
    <app:draft>{draft_flag}</app:draft>
  </app:control>
</entry>'''
    return entry_xml


def post_to_hatena(title: str, article_md: str) -> None:
    """MarkdownをHTML化して、はてなブログに1件投稿する"""
    if not (HATENA_ID and HATENA_API_KEY and HATENA_BLOG_ID):
        raise RuntimeError("HATENA_ID / HATENA_API_KEY / HATENA_BLOG_ID が設定されていません。")

    endpoint = f"https://blog.hatena.ne.jp/{HATENA_ID}/{HATENA_BLOG_ID}/atom/entry"

    # Markdown -> HTML
    content_html = markdown(article_md, extensions=["extra", "tables"])

    # カテゴリはお好みで調整
    categories = ["ミャンマー情勢", "国際ニュース"]

    entry_xml = build_hatena_entry_xml(
        title=title,
        content_html=content_html,
        categories=categories,
        draft=False,  # 下書きにしたい場合は True
    )

    print("[INFO] Posting entry to Hatena Blog...")
    resp = requests.post(
        endpoint,
        data=entry_xml.encode("utf-8"),
        headers={"Content-Type": "application/atom+xml; charset=utf-8"},
        auth=(HATENA_ID, HATENA_API_KEY),
        timeout=30,
    )

    if not resp.ok:
        raise RuntimeError(f"Failed to post to Hatena Blog: {resp.status_code} {resp.text}")

    print(f"[INFO] Posted successfully to Hatena Blog. status={resp.status_code}")


# ================== メイン処理 ==================

def main():
    print("==== Myanmar News Auto Poster (B: RSS翻訳＋要約モード) ====")

    # Geminiクライアントの準備
    try:
        client = create_gemini_client()
    except Exception as e:
        print(f"[ERROR] Failed to create Gemini client: {e}")
        return

    # 既に記事化済みのIDを読み込み
    seen_ids = load_seen_articles()
    print(f"[INFO] Loaded {len(seen_ids)} seen article IDs.")

    # 新規記事を1件選定
    article = select_new_article(seen_ids)
    if not article:
        print("[INFO] 新規に翻訳・要約する記事が見つかりませんでした。処理を終了します。")
        return

    # Geminiで日本語記事生成
    try:
        article_md = summarize_and_translate_with_gemini(client, article)
    except Exception as e:
        print(f"[ERROR] Gemini article generation failed: {e}")
        return

    # タイトルは生成されたMarkdownの1行目から取得するのが自然だが、
    # ここでは元記事タイトルをそのまま使うか、Markdown先頭行が # ならそれを優先してもよい
    generated_title = None
    if article_md.startswith("#"):
        first_line = article_md.splitlines()[0].lstrip("#").strip()
        if first_line:
            generated_title = first_line

    title_for_hatena = generated_title or article["title"]

    # はてなブログへ投稿
    try:
        post_to_hatena(title_for_hatena, article_md)
    except Exception as e:
        print(f"[ERROR] Failed to post to Hatena Blog: {e}")
        return

    # 投稿に成功したら、この記事をseenに追加
    seen_ids.add(article["unique_id"])
    save_seen_articles(seen_ids)
    print("[INFO] Completed B: RSS翻訳モード run.")


if __name__ == "__main__":
    main()
