import os
import json
import textwrap
import datetime
import feedparser
import requests
from bs4 import BeautifulSoup
import markdown
import google.genai as genai

# =========================================================
# 設定
# =========================================================

# Gemini モデル名は環境変数から取得（404 が出る場合は Actions 側で差し替え）
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")

# はてなブログ設定（GitHub Actions の env / secrets から渡す想定）
HATENA_ID = os.environ.get("HATENA_ID")
HATENA_API_KEY = os.environ.get("HATENA_API_KEY")
HATENA_BLOG_ID = os.environ.get("HATENA_BLOG_ID")

# 投稿済み記事ID保存用ファイル
SEEN_ARTICLES_FILE = "seen_articles.json"

# 対象RSSフィード（ミャンマー関連記事の代表サイト）
RSS_SOURCES = [
    {
        "name": "The Irrawaddy (English)",
        "url": "https://www.irrawaddy.com/feed",
    },
    {
        "name": "Myanmar Now (English)",
        "url": "https://www.myanmar-now.org/en/feed",
    },
    {
        "name": "Frontier Myanmar (English)",
        "url": "https://www.frontiermyanmar.net/en/feed",
    },
]

# Gemini クライアント
client = genai.Client(
    api_key=os.environ.get("GEMINI_API_KEY")
)

print(f"[INFO] Using Gemini model: {GEMINI_MODEL}")


# =========================================================
# ユーティリティ
# =========================================================

def load_seen_article_ids():
    """既に投稿済みの記事ID一覧を読み込む"""
    if not os.path.exists(SEEN_ARTICLES_FILE):
        print("[INFO] Loaded 0 seen article IDs.")
        return []

    try:
        with open(SEEN_ARTICLES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            print(f"[INFO] Loaded {len(data)} seen article IDs.")
            return data
        else:
            print("[WARN] seen_articles.json format invalid, resetting.")
            return []
    except Exception as e:
        print("[WARN] Failed to load seen_articles.json:", e)
        return []


def save_seen_article_ids(ids):
    """投稿済みの記事ID一覧を保存"""
    try:
        with open(SEEN_ARTICLES_FILE, "w", encoding="utf-8") as f:
            json.dump(ids, f, ensure_ascii=False, indent=2)
        print(f"[INFO] Saved {len(ids)} seen article IDs.")
    except Exception as e:
        print("[ERROR] Failed to save seen_articles.json:", e)


def fetch_rss_entries(url: str):
    """RSSフィードから記事一覧を取得"""
    d = feedparser.parse(url)
    entries = d.entries or []
    return entries


def pick_new_article(entries, seen_ids):
    """
    まだ投稿していない記事を1件選ぶ。
    記事IDは entry.id があればそれを、なければ link を使う。
    """
    for entry in entries:
        article_id = getattr(entry, "id", None) or getattr(entry, "link", None)
        if not article_id:
            continue
        if article_id not in seen_ids:
            return entry, article_id
    return None, None


def fetch_full_article_content(url: str) -> str:
    """
    元記事URLから本文をできるだけ多く取得する。
    失敗した場合は空文字を返す。
    """
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print("[WARN] Failed to fetch article content:", e)
        return ""

    soup = BeautifulSoup(resp.text, "lxml")

    # よくある記事コンテナを優先して拾う
    candidates = []
    if soup.find("article"):
        candidates.append(soup.find("article"))
    if soup.find("main"):
        candidates.append(soup.find("main"))
    candidates.append(soup.body)

    text = ""
    for c in candidates:
        if not c:
            continue
        # 不要なタグを削除
        for tag in c.find_all(["script", "style", "nav", "footer", "header", "form"]):
            tag.decompose()
        t = c.get_text(separator="\n")
        t = "\n".join(line.strip() for line in t.splitlines() if line.strip())
        if len(t) > len(text):
            text = t

    # 長すぎる場合は適度にカット（プロンプト過大防止）
    max_chars = 8000
    if len(text) > max_chars:
        text = text[:max_chars]

    return text.strip()


def generate_article_with_gemini(source_name, article_title, article_url, article_content):
    """
    英語記事をもとに、日本語の「要約＋再構成」記事を Gemini で生成する。
    戻り値は Markdown 文字列（失敗時は None）。
    """
    if not article_content:
        # コンテンツが取れていない場合は、summary などを親側で渡してくる前提
        print("[WARN] Article content is empty. Using fallback text in prompt.")

    prompt = f"""
あなたはミャンマー政治・経済の専門ニュース編集者です。

### タスク
以下の英語ニュース記事を読み、その内容をもとに **日本語の記事** を作成してください。

- テーマ: ミャンマー情勢（政治・経済・社会全般）
- 想定読者: 日本の一般読者（ミャンマー情勢に関心はあるが専門家ではない）
- 記事構成:
  1. 冒頭で「この記事の主題」を 2〜3文で端的に説明
  2. 重要な事実・数字・関係者・地名などを整理してわかりやすく説明
  3. 背景や文脈（なぜ重要なのか）を簡潔に補足
  4. 今後の見通しや影響があれば触れる

### 文章スタイル
- 日本語: 「です・ます調」
- 分量: 1,000〜1,500文字程度を目安
- 固有名詞（人名・地名・組織名）はできるだけ維持しつつ、カタカナ表記や補足説明を入れてください。
- 陰謀論や推測は書かず、元記事の事実関係に忠実に。

### 出力フォーマット（Markdown）
- 1つの H1 見出し（記事タイトル）
- 複数の H2 / H3 見出しで記事を構成
- 箇条書きが適切な箇所では bullet list を使う
- 最後に「元記事: {article_url}」と記載

### 英語元記事（{source_name}）
タイトル: {article_title}

本文:
{article_content}
    """.strip()

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
        )
        # google-genai のレスポンスは response.text に Markdown 全文が入る
        return response.text
    except Exception as e:
        print("[ERROR] Gemini article generation failed:", e)
        return None


def escape_xml(text: str) -> str:
    """Atom投稿用に最低限のXMLエスケープを行う"""
    if text is None:
        return ""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def post_to_hatena(title: str, content_markdown: str) -> bool:
    """
    はてなブログに Markdown 記事を投稿する。
    成功時 True / 失敗時 False を返す。
    """
    if not (HATENA_ID and HATENA_API_KEY and HATENA_BLOG_ID):
        print("[ERROR] Hatena blog environment variables are not set.")
        return False

    endpoint = f"https://blog.hatena.ne.jp/{HATENA_ID}/{HATENA_BLOG_ID}/atom/entry"

    updated = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:app="http://www.w3.org/2007/app">
  <title>{escape_xml(title)}</title>
  <updated>{updated}</updated>
  <content type="text/x-markdown">{escape_xml(content_markdown)}</content>
  <category term="ミャンマー情勢" />
  <app:control>
    <app:draft>no</app:draft>
  </app:control>
</entry>
""".strip()

    headers = {
        "Content-Type": "application/xml; charset=utf-8",
    }

    try:
        resp = requests.post(
            endpoint,
            data=xml.encode("utf-8"),
            headers=headers,
            auth=(HATENA_ID, HATENA_API_KEY),
            timeout=15,
        )
        if 200 <= resp.status_code < 300:
            print("[INFO] Hatena blog post succeeded. status =", resp.status_code)
            return True
        else:
            print("[ERROR] Hatena blog post failed. status =", resp.status_code)
            print(resp.text)
            return False
    except Exception as e:
        print("[ERROR] Exception while posting to Hatena:", e)
        return False


# =========================================================
# メイン処理
# =========================================================

def main():
    print("==== Myanmar News Auto Poster (B: RSS翻訳＋要約モード) ====")

    seen_ids = load_seen_article_ids()

    selected_entry = None
    selected_id = None
    selected_source = None

    # 各RSSを順に見て、まだ投稿していない記事を1件見つける
    for source in RSS_SOURCES:
        name = source["name"]
        url = source["url"]
        print(f"[INFO] Checking RSS source: {name} ({url})")

        entries = fetch_rss_entries(url)
        print(f"[INFO] RSS fetched: {url} / entries = {len(entries)}")

        entry, article_id = pick_new_article(entries, seen_ids)
        if entry is not None:
            selected_entry = entry
            selected_id = article_id
            selected_source = name
            print(f"[INFO] Selected new article from {name}: {entry.title}")
            break

    if selected_entry is None:
        print("[INFO] No new article found in all RSS sources. Exit.")
        return

    entry = selected_entry
    article_id = selected_id
    source_name = selected_source

    article_title = getattr(entry, "title", "(no title)")
    article_url = getattr(entry, "link", "")

    # 元記事本文をできるだけ取得
    content = ""
    if article_url:
        content = fetch_full_article_content(article_url)

    # それでも空なら、summary/description などを fallback として使う
    if not content:
        summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
        fallback_text = f"Title: {article_title}\n\nSummary:\n{summary}"
        content = fallback_text

    print("[INFO] Generating Japanese article with Gemini...")

    article_md = generate_article_with_gemini(
        source_name=source_name,
        article_title=article_title,
        article_url=article_url,
        article_content=content,
    )

    if not article_md:
        print("[ERROR] Gemini failed to generate article. Exit without posting.")
        # 失敗した記事は seen_ids に入れない（次回再トライのため）
        return

    # 念のため、末尾に元記事リンクがなければ追加
    if "元記事:" not in article_md and article_url:
        article_md = article_md.rstrip() + f"\n\n元記事: {article_url}\n"

    # 記事タイトルは Markdown の一行目の # から取る or entry.title を使う
    first_line = article_md.lstrip().splitlines()[0] if article_md.strip() else ""
    if first_line.startswith("#"):
        post_title = first_line.lstrip("#").strip()
    else:
        post_title = article_title

    print("[INFO] Posting article to Hatena blog...")
    ok = post_to_hatena(post_title, article_md)

    if not ok:
        print("[ERROR] Failed to post article to Hatena. Exit without updating seen list.")
        return

    # 正常に投稿できたら、この記事のIDを seen に追加して保存
    if article_id not in seen_ids:
        seen_ids.append(article_id)
        save_seen_article_ids(seen_ids)


if __name__ == "__main__":
    main()
