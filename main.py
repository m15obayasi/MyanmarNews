import os
import json
import time
import requests
import re
from bs4 import BeautifulSoup
from lxml import etree
from google import genai
import markdown

SEEN_FILE = "seen_articles.json"
MODEL = "gemini-2.0-flash"

RSS_SOURCES = [
    ("Irrawaddy", "https://www.irrawaddy.com/feed"),
]


# ============================
# seen 記録
# ============================

def load_seen():
    if os.path.exists(SEEN_FILE):
        return set(json.loads(open(SEEN_FILE, "r", encoding="utf-8").read()))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)


# ============================
# RSS（安定版）
# ============================

def fetch_rss_xml(url, source_name):
    print(f"Fetching RSS: {source_name}")

    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, timeout=20, headers=headers)
    except:
        print(f"接続エラー → {source_name}")
        return []

    if r.status_code != 200:
        print(f"RSS取得失敗({source_name}): {r.status_code}")
        return []

    xml_text = r.content.decode("utf-8", errors="replace")

    try:
        root = etree.fromstring(xml_text.encode("utf-8"))
    except Exception as e:
        print(f"XMLパース失敗({source_name}):", e)
        return []

    items = root.xpath("//item")

    articles = []
    for item in items:
        extract = lambda xp: item.xpath(xp)[0].text.strip() if item.xpath(xp) else ""
        link = extract("link")
        if not link:
            continue

        title = extract("title")
        description = BeautifulSoup(extract("description") or "", "html.parser").get_text().strip()

        articles.append({
            "source": source_name,
            "title": title,
            "summary": description,
            "content": description,
            "link": link,
        })

    print(f"取得件数({source_name}): {len(articles)}")
    return articles


# ============================
# Gemini：日本語タイトル生成
# ============================

def generate_jp_title(raw_title):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
次の英語ニュースタイトルを「日本語の自然なニュースタイトル」に翻訳して下さい。

【条件】
- 日本のニュース記事の語順にする
- 30文字以内
- 1行のみ出力
- タイトル案など余計な語句は絶対に書かない

英語タイトル：
{raw_title}
"""

    res = client.models.generate_content(model=MODEL, contents=prompt).text

    # 最初の1行だけ
    line = res.splitlines()[0].strip()

    # 不要記号除去
    line = re.sub(r"^[\-\•\*【「『(（\s]+", "", line)
    line = re.sub(r"[」』】)）\s]+$", "", line)

    return line


# ============================
# Gemini：本文生成
# ============================

def generate_body(article):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
あなたは日本語ニュース記者です。
Markdown形式で以下の構成を厳守して本文だけを書いてください。

== 出力フォーマット ==
元記事URL: {article["link"]}

## 概要
（要点を4〜6文）

## 背景
（政治状況・関係勢力など）

## 今後の見通し
（短くても良い）

## 推測
（波及効果・国際的影響）

【禁止】
- タイトルを書かない
- 指示文を出力しない
- セクション名を変更しない

英語タイトル:
{article["title"]}

要約:
{article["summary"]}

本文:
{article["content"]}
"""

    res = client.models.generate_content(model=MODEL, contents=prompt).text
    return fix_markdown(res)


# ============================
# Markdown 正規化（体裁の核）
# ============================

def fix_markdown(md):
    # 不可視文字除去（ゼロ幅スペースなど）
    md = re.sub(r"[\u200b\u200c\u200d\uFEFF]", "", md)

    # 全角記号の統一（Markdown崩壊防止）
    md = md.replace("–", "-").replace("—", "-")

    lines = md.splitlines()
    new_lines = []

    for line in lines:
        # 見出し強制統一
        if line.strip().startswith("##"):
            # ##概要 → <h2>概要</h2>
            title = line.replace("##", "").strip()
            new_lines.append(f"<h2>{title}</h2>")
        else:
            new_lines.append(line.strip())

    cleaned = "\n".join(new_lines)

    # 空行は <br> に変換
    cleaned = cleaned.replace("\n\n", "<br><br>")

    return cleaned.strip()


# ============================
# はてなブログ投稿
# ============================

def post_hatena(title, html_body):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    # Markdown ではなく、すでに最終 HTML を渡す
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{title}</title>
  <content type="text/html">
{html_body}
  </content>
</entry>"""

    url = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    r = requests.post(
        url,
        data=xml.encode("utf-8"),
        auth=(hatena_id, api_key),
        headers={"Content-Type": "application/xml"}
    )

    if r.status_code in [200, 201]:
        print("投稿成功")
        return True

    print("投稿失敗:", r.status_code, r.text)
    return False


# ============================
# メイン処理
# ============================

def main():
    print("==== Myanmar News Auto Poster ====")

    seen = load_seen()
    new_seen = set(seen)

    all_articles = []
    for name, url in RSS_SOURCES:
        all_articles.extend(fetch_rss_xml(url, name))

    fresh = [a for a in all_articles if a["link"] not in seen]
    print("新規記事:", len(fresh))

    if not fresh:
        print("新記事なし。終了します。")
        return

    for a in fresh:
        print("=== タイトル生成 ===")
        jp_title = generate_jp_title(a["title"])
        print("→", jp_title)

        print("=== 本文生成 ===")
        html_body = generate_body(a)

        ok = post_hatena(jp_title, html_body)
        if ok:
            new_seen.add(a["link"])
            save_seen(new_seen)
            time.sleep(5)


if __name__ == "__main__":
    main()
