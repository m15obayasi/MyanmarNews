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
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except:
            return set()
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
# Gemini タイトル生成 + 本文生成
# --------------------------------------------------
def generate_article(article):
    print("Generating article with Gemini AI...")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
以下の2つを JSON で返してください。

1. "title": 日本語タイトル（記事内容を正確に要約。短め）
2. "body": 以下のMarkdown形式で本文。タイトルは本文に含めない。

# 必ず守る形式

## 概要
（ニュースの要点）

## 背景
（背景説明。書けない場合は空欄）

## 今後の見通し
（予測不能な場合は出力しない）

## 推測
（背景や見通しが書けない場合、必須で記述）

# 注意
- 「翻訳案」「警告」など余計な文を絶対に入れない
- 指示文をそのまま本文に書かない
- JSON のみ返す（Markdown を JSON の body に入れる）

--- 元記事情報 ---
英語タイトル: {article["title"]}
概要: {article["summary"]}
本文抜粋: {article["content"]}
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    # Gemini の返す JSON を確実にパース
    try:
        data = json.loads(response.text)
    except:
        print("JSONパース失敗。Gemini出力を表示:")
        print(response.text)
        raise

    title = data["title"]
    body = data["body"]

    # ---- 整形 ----

    # 連続空行の削除
    body = re.sub(r"\n{3,}", "\n\n", body)

    # 見出しの手前後の余白整理
    body = re.sub(r"\n+\s*##", "\n\n##", body)

    # 先頭に元記事URLを追加
    final_md = f"元記事URL: {article['link']}\n\n{body}"

    return title, final_md


# --------------------------------------------------
# Hatena Blog 投稿
# --------------------------------------------------
def post_to_hatena(title, content_md):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    # Markdown → HTML（壊れにくい設定）
    content_html = markdown.markdown(
        content_md,
        extensions=["extra", "sane_lists"]
    )

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

    print("Posting to Hatena Blog...")

    r = requests.post(url, data=xml.encode("utf-8"), auth=(hatena_id, api_key))

    if r.status_code not in [200, 201]:
        print("Hatena投稿失敗:", r.status_code, r.text)
        return False

    print("Hatena投稿成功")
    return True


# --------------------------------------------------
# メイン
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

            # HTML除去
            clean_content = BeautifulSoup(content, "html.parser").get_text()

            new_articles.append({
                "id": link,
                "title": e.get("title", ""),
                "summary": summary,
                "content": clean_content,
                "link": link
            })

    print("新規記事:", len(new_articles))

    if not new_articles:
        print("新記事なし。終了します。")
        return

    for article in new_articles:
        # タイトルと本文を生成
        title, md = generate_article(article)

        ok = post_to_hatena(title, md)

        if ok:
            seen.add(article["id"])
            save_seen(seen)


# --------------------------------------------------
if __name__ == "__main__":
    main()
