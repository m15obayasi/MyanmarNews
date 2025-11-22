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
# 英語タイトル → 自然な日本語タイトル
# --------------------------------------------------
def generate_jp_title(client, english_title):
    prompt = f"""
次の英語ニュースタイトルを、自然で硬派な日本語ニュースタイトルに翻訳してください。
タイトル以外は絶対に書かないでください。

英語タイトル:
{english_title}
"""
    res = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )
    title = res.text.strip()
    title = title.replace("#", "").replace("*", "").strip()
    return title


# --------------------------------------------------
# Gemini で Markdown 本文生成
# --------------------------------------------------
def generate_markdown(client, article):
    print("Generating article with Gemini AI...")

    prompt = f"""
以下の【出力フォーマット】のみで、日本語の記事本文を生成してください。
絶対にタイトルは本文に含めないでください。
文章以外の注意書き・翻訳案・候補案は禁止。

【出力フォーマット】
元記事URL: {article["link"]}

## 概要
（ニュース要約）

## 背景
（背景説明。無ければ空欄でよい）

## 今後の見通し
（予測可能なら記述。難しい場合は省略して、代わりに下記を出力）

## 推測
（このニュースが及ぼす可能性のある影響）


--- 元記事情報 ---
英語タイトル:
{article["title"]}

概要:
{article["summary"]}

本文の抜粋:
{article["content"]}
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    text = response.text

    # タイトル混入を削除
    lines = text.split("\n")
    if lines[0].startswith("#"):
        lines = lines[1:]
    text = "\n".join(lines).lstrip()

    # 3行以上の連続改行 → 2行へ
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 見出し前に空行確保
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

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    seen = load_seen()

    RSS_SOURCES = [
        ("Irrawaddy", "https://www.irrawaddy.com/feed"),
    ]

    new_articles = []

    # RSS取得
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

    # 投稿処理
    for article in new_articles:

        # 日本語タイトル生成
        jp_title = generate_jp_title(client, article["title"])
        print("生成タイトル:", jp_title)

        # 本文生成
        md = generate_markdown(client, article)

        ok = post_to_hatena(jp_title, md)

        if ok:
            seen.add(article["id"])
            save_seen(seen)


# --------------------------------------------------
if __name__ == "__main__":
    main()
