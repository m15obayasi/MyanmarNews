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
        get = lambda xp: item.xpath(xp)[0].text if item.xpath(xp) else ""

        link = get("link").strip()
        if not link:
            continue

        title = get("title").strip()
        description = BeautifulSoup(get("description") or "", "html.parser").get_text().strip()

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
次の英語ニュースタイトルを「日本語の自然なニュースタイトル」へ翻訳してください。

【必須条件】
- 日本の報道記事の語順にする
- 30文字以内
- 「タイトル案」「翻訳結果」などの余計な言葉を一切書かない
- 出力はタイトル1行のみ

英語タイトル：
{raw_title}
"""

    res = client.models.generate_content(model=MODEL, contents=prompt).text

    # ---- 最初の行だけを抽出して整形 ----
    line = res.splitlines()[0].strip()

    # 変な記号除去（AI がつけてしまうことがある）
    line = re.sub(r"^[\-\•\*【「『(（\s]+", "", line)
    line = re.sub(r"[」』】)）\s]+$", "", line)

    return line


# ============================
# Gemini：本文生成
# ============================

def generate_body(article):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
あなたは日本語の報道記者です。
必ず以下の Markdown フォーマットだけを出力してください。

（注意）
- 絶対にタイトルを書かない
- タイトル案なども禁止
- 指示文を出力しない

== 出力フォーマット ==

元記事URL: {article["link"]}

## 概要
（ニュースの要点を4〜6文）

## 背景
（政治状況、地理、関係勢力を整理）

## 今後の見通し
（予測が難しい場合は短くても良い）

## 推測
（波及効果・国際的影響を論理的に）

==============================

【元データ】
英語タイトル:
{article["title"]}

概要:
{article["summary"]}

本文:
{article["content"]}

"""

    res = client.models.generate_content(model=MODEL, contents=prompt).text
    return clean_markdown(res)


# ============================
# Markdown整形（重要）
# ============================

def clean_markdown(md):
    # 行頭のスペースを除去
    md = "\n".join([line.lstrip() for line in md.splitlines()])

    # ## の前後に空行を強制
    md = re.sub(r"\n*##", r"\n\n##", md)

    # 「##概要」→「## 概要」
    md = re.sub(r"##\s*", "## ", md)

    # 連続改行を2つまで
    md = re.sub(r"\n{3,}", "\n\n", md)

    # 最初の行を「元記事URL:」に強制
    lines = md.splitlines()
    if not lines[0].startswith("元記事URL:"):
        for i, line in enumerate(lines):
            if line.startswith("元記事URL:"):
                lines = [line] + lines[:i] + lines[i+1:]
                break
        md = "\n".join(lines)

    return md.strip()


# ============================
# はてなブログ投稿
# ============================

def post_hatena(title, md):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    html = markdown.markdown(md, extensions=["extra"])

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{title}</title>
  <content type="text/html">
{html}
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
    else:
        print("投稿失敗:", r.status_code, r.text)
        return False


# ============================
# メイン
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
        print("終了")
        return

    for a in fresh:
        print("\n=== タイトル生成 ===")
        jp_title = generate_jp_title(a["title"])
        print("→", jp_title)

        print("=== 本文生成 ===")
        md_body = generate_body(a)

        ok = post_hatena(jp_title, md_body)
        if ok:
            new_seen.add(a["link"])
            save_seen(new_seen)
            time.sleep(5)


if __name__ == "__main__":
    main()
