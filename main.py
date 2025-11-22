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


# --------------------------------------------------
# seen_articles.json 読み書き
# --------------------------------------------------
def load_seen():
    return set(json.load(open(SEEN_FILE, "r", encoding="utf-8"))) if os.path.exists(SEEN_FILE) else set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)


# --------------------------------------------------
# RSS 取得（Irrawaddy が変な encoding を返す時があるので try/catch）
# --------------------------------------------------
def fetch_rss(url, name):
    print(f"Fetching RSS: {name}")
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        print("RSS Error:", e)
        return []

    if feed.bozo:
        print("RSS Parse Error:", feed.bozo_exception)
        return []

    return feed.entries


# --------------------------------------------------
# 日本語ニュースタイトル生成
# --------------------------------------------------
def generate_title(client, article):
    prompt = f"""
以下の英語ニュースタイトルを、日本語の記事タイトルとして自然な形に変換してください。
・難しい語順は日本語らしく並べ替える
・固有名詞はそのまま残す
・短く簡潔に
・出力はタイトルのみ
・語尾に句点は不要

英語タイトル:
{article["title"]}
"""

    res = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    title = res.text.strip()

    # タイトル整形（改行削除・余計な記号除去）
    title = re.sub(r"\s+", " ", title)
    title = title.replace("#", "").strip()

    return title


# --------------------------------------------------
# 本文（Markdown）生成
# --------------------------------------------------
def generate_article_body(client, article):
    prompt = f"""
あなたは日本語ニュース編集者です。
以下の条件で Markdown 記事を作成してください。

# 制約
- 冒頭に「## 参照元」を追加し、その中に元記事URLを記載
- 見出しは「## 参照元」「## 概要」「## 背景」「## 今後の見通し」「## 推測」
- タイトルは出力しない（本文のみ）
- 余計な注意書き・提案・補足は禁止
- 報道調で淡々と書く

# 出力フォーマット（厳守）

## 参照元
- 元記事URL: {article["link"]}

## 概要
（要約）

## 背景
（背景説明）

## 今後の見通し
（不明なら「不明確」などの短い表現）

## 推測
（このニュースが与える影響。内容不足時はここで補完）

--- 元記事情報 ---
英語タイトル:
{article["title"]}

概要:
{article["summary"]}

本文抜粋:
{article["content"]}
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    md = response.text

    # Markdown整形
    md = md.replace("\r", "")
    md = re.sub(r"\n{3,}", "\n\n", md)

    # 見出し修正
    md = re.sub(r"\n+\s*##", "\n\n##", md)

    return md.strip()


# --------------------------------------------------
# はてな投稿（Markdown → HTML）
# --------------------------------------------------
def post_to_hatena(title, md):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    content_html = markdown.markdown(md, extensions=["extra"])

    url = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    # XML内に <content> が壊れないよう必ず <![CDATA[]>] で囲う
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{title}</title>
  <content type="text/html"><![CDATA[
{content_html}
  ]]></content>
</entry>
"""

    print("=== 投稿中 ===")

    r = requests.post(url, data=xml.encode("utf-8"), auth=(hatena_id, api_key))

    if r.status_code not in [200, 201]:
        print("投稿失敗:", r.status_code, r.text)
        return False

    print("→ 投稿成功！")
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

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    new_articles = []

    # RSS 読み込み
    for name, url in RSS_SOURCES:
        entries = fetch_rss(url, name)
        print(f"取得件数({name}):", len(entries))

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
        print("新記事なし。終了")
        return

    # 記事処理
    for article in new_articles:
        print("\n=== タイトル生成 ===")
        title = generate_title(client, article)
        print("→", title)

        print("=== 本文生成 ===")
        md = generate_article_body(client, article)

        print("=== 投稿 ===")
        ok = post_to_hatena(title, md)

        if ok:
            seen.add(article["id"])
            save_seen(seen)

        time.sleep(3)  # Hatena API の負荷軽減


# --------------------------------------------------
if __name__ == "__main__":
    main()
