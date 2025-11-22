import os
import json
import time
import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
import markdown
import re

SEEN_FILE = "seen_articles.json"

# =========================================================
# Utility: JSON 読み込み/保存
# =========================================================
def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except:
            return set()
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False, indent=2)


# =========================================================
# Utility: Gemini 呼び出し（429対応）
# =========================================================
def call_gemini_with_retry(client, model, prompt, max_retries=5):
    for i in range(max_retries):
        try:
            res = client.models.generate_content(model=model, contents=prompt)
            return res.text
        except Exception as e:
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                wait_sec = 50 + i * 10
                print(f"[429] Quota exceeded. Waiting {wait_sec} sec...")
                time.sleep(wait_sec)
                continue
            print("Gemini error:", e)
            raise
    raise RuntimeError("Gemini retry limit exceeded")


# =========================================================
# RSS 取得
# =========================================================
def fetch_rss(url, name):
    print(f"Fetching RSS: {name} ...")
    feed = feedparser.parse(url)
    if feed.bozo:
        print(f"RSS取得失敗（{name}）:", feed.bozo_exception)
        return []
    return feed.entries


# =========================================================
# タイトル翻訳（完全固定）
# =========================================================
def translate_title(client, title_en):
    prompt = f"""
以下の英語ニュースタイトルを「日本語の自然なニュースタイトル（1行だけ）」として翻訳してください。

# 厳守ルール
- 出力は **タイトル1行のみ**
- 箇条書き禁止
- 複数候補禁止
- 「提案」「候補」「以下に示す」「考えられる」など禁止
- 補足文禁止
- 丁寧語禁止
- 文頭に余計な言葉禁止
- 新聞の見出し調で、簡潔に1本

英語タイトル:
{title_en}

出力：日本語タイトル 1行のみ
"""

    print("Translating title...")
    title = call_gemini_with_retry(client, "gemini-2.0-flash", prompt)
    title = title.strip().split("\n")[0]
    title = re.sub(r"^[#\-\*\•：:\s]+", "", title).strip()

    banned = ["いずれ", "候補", "提案", "示し", "考えら", "以下"]
    if any(b in title for b in banned):
        print("⚠ 不正なタイトル検出 → 再翻訳")
        return translate_title(client, title_en)

    print("日本語タイトル:", title)
    return title


# =========================================================
# Markdown レイアウト補正
# =========================================================
def fix_markdown_layout(md):
    md = md.replace("\r\n", "\n")

    md = re.sub(r"\n*(## .+?)\n*", r"\n\n\1\n\n", md)

    md = re.sub(r"\n{3,}", "\n\n", md)

    md = re.sub(r"^\s+", "", md, flags=re.MULTILINE)

    return md.strip()


# =========================================================
# Gemini で本文生成
# =========================================================
def generate_markdown(client, article):
    print("Generating article with Gemini...")

    prompt = f"""
以下のニュースをもとに、日本語の記事本文を Markdown 形式で生成してください。

# 出力フォーマット（厳守）
元記事URL: {article["link"]}

## 概要
（要約）

## 背景
（背景説明。なければ空欄でよい）

## 今後の見通し
（予測不能なら記述しない）

## 推測
（ニュースが与える可能性のある影響）

# 禁止事項
- タイトルを出力しない
- 「候補案」「翻訳案」など禁止
- 前置き禁止
- 余計な解説禁止

--- 英文データ ---
英語タイトル:
{article["title"]}

概要:
{article["summary"]}

本文サマリ:
{article["content"]}
"""

    text = call_gemini_with_retry(client, "gemini-2.0-flash", prompt)

    text = fix_markdown_layout(text)
    return text


# =========================================================
# はてなブログへ投稿（Markdown → HTML）
# =========================================================
def post_to_hatena(title, content_md):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    content_html = markdown.markdown(content_md, extensions=["extra"])

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:app="http://www.w3.org/2007/app">

  <title>{title}</title>

  <content type="text/html">
{content_html}
  </content>

</entry>
"""

    url = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"
    headers = {"Content-Type": "application/xml"}

    print("Posting to Hatena Blog...")
    r = requests.post(url, data=xml.encode("utf-8"), auth=(hatena_id, api_key))

    if r.status_code not in [200, 201]:
        print("Hatena投稿失敗:", r.status_code, r.text)
        return False

    print("Hatena投稿成功")
    return True


# =========================================================
# メイン処理
# =========================================================
def main():
    print("==== Myanmar News Auto Poster ====")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    seen = load_seen()

    RSS_SOURCES = [
        ("Irrawaddy", "https://www.irrawaddy.com/feed"),
    ]

    new_articles = []

    for name, url in RSS_SOURCES:
        entries = fetch_rss(url, name)
        print("取得件数:", len(entries))

        for e in entries:
            link = e.get("link")
            if not link or link in seen:
                continue

            summary = BeautifulSoup(e.get("summary", ""), "html.parser").get_text()
            content_raw = e.get("content", [{"value": summary}])[0]["value"]
            content = BeautifulSoup(content_raw, "html.parser").get_text()

            new_articles.append({
                "id": link,
                "title": e.get("title", ""),
                "summary": summary,
                "content": content,
                "link": link
            })

    print("新規記事:", len(new_articles))

    if not new_articles:
        print("新記事なし。終了します。")
        return

    for article in new_articles:
        jp_title = translate_title(client, article["title"])
        md = generate_markdown(client, article)

        ok = post_to_hatena(jp_title, md)

        if ok:
            seen.add(article["id"])
            save_seen(seen)


# =========================================================
if __name__ == "__main__":
    main()
