import os
import json
import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
import markdown
import re

SEEN_FILE = "seen_articles.json"


# --------------------------------------------------
# seen_articles.json の読み込み/保存
# --------------------------------------------------
def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False, indent=2)


# --------------------------------------------------
# RSS 取得
# --------------------------------------------------
def fetch_rss(url, name):
    print(f"Fetching RSS: {name} ...")
    feed = feedparser.parse(url)
    if feed.bozo:
        print(f"RSS取得失敗（{name}）:", feed.bozo_exception)
        return []
    return feed.entries


# --------------------------------------------------
# Gemini で Markdown 記事生成
# --------------------------------------------------
def generate_markdown(article):
    print("Generating article with Gemini AI...")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
# 以下の形式で日本語の記事を生成してください。

## 必ず守るルール
- 見出しは「概要」「背景」「今後の見通し」または「推測」のいずれか
- タイトルは出力しない（本文に含めない）
- 「翻訳案」「候補案」「警告文」「補足」などは絶対に書かない
- 余計な説明文は絶対に書かない

## 出力フォーマット
元記事URL: {article["link"]}

## 概要
（要約）

## 背景
（背景説明。無い場合は空白で良い）

## 今後の見通し
（予測できない場合は省略し、代わりに下記を出力）

## 推測
（このニュースが今後与える可能性のある影響）

--- 元記事情報 ---
英語タイトル:
{article["title"]}

概要:
{article["summary"]}

本文（英語の抜粋・サマリ）:
{article["content"]}

"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )
    text = response.text

    # --------------------------------------------------
    # ① もし Gemini がタイトルを勝手に出したら削除
    # --------------------------------------------------
    lines = text.split("\n")
    if lines[0].startswith("#"):
        lines = lines[1:]

    text = "\n".join(lines).lstrip()

    # --------------------------------------------------
    # ② 連続する空白行を1つに削減
    # --------------------------------------------------
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 見出し前後の空白を整形
    text = re.sub(r"\n+\s*##", "\n\n##", text)

    return text


# --------------------------------------------------
# はてなブログ投稿（Markdown → HTML）
# --------------------------------------------------
def post_to_hatena(title, content_md):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    # Markdown → HTML
    content_html = markdown.markdown(content_md, extensions=["extra"])

    url = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:app="http://www.w3.org/2007/app">

  <title>{title}</title>

  <content type="text/html">
{content_html}
  </content>

</entry>
"""

    headers = {"Content-Type": "application/xml"}

    print("Posting to Hatena Blog...")
    r = requests.post(url, data=xml.encode("utf-8"), auth=(hatena_id, api_key))

    if r.status_code not in [200, 201]:
        print("Hatena投稿失敗:", r.status_code, r.text)
        return False

    print("Hatena投稿成功")
    return True


# --------------------------------------------------
# メイン処理
# --------------------------------------------------
def main():
    print("==== Myanmar News Auto Poster ====")

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
            content = e.get("content", [{"value": summary}])[0]["value"]

            new_articles.append({
                "id": link,
                "title": e.get("title", ""),
                "summary": summary,
                "content": BeautifulSoup(content, "html.parser").get_text(),
                "link": link
            })

    print("新規記事:", len(new_articles))

    if not new_articles:
        print("新記事なし。終了します。")
        return

    for article in new_articles:
        md = generate_markdown(article)

        # タイトルは英語タイトルをそのまま翻訳せずに使う
        safe_title = article["title"]

        ok = post_to_hatena(safe_title, md)

        if ok:
            seen.add(article["id"])
            save_seen(seen)


# --------------------------------------------------
if __name__ == "__main__":
    main()
