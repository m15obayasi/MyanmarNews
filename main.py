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
# RSS取得（Irrawaddy対策）
# --------------------------------------------------
def fetch_rss(url, name):
    print(f"Fetching RSS: {name} ...")

    try:
        r = requests.get(url, timeout=10)
        r.encoding = r.apparent_encoding  # 自動推定エンコーディング使用
        xml_text = r.text

        # XMLヘッダの encoding を強制的に UTF-8 に書き換える
        xml_text = re.sub(
            r'<\?xml[^>]*encoding=["\'].*?["\']',
            '<?xml version="1.0" encoding="UTF-8"',
            xml_text
        )

        feed = feedparser.parse(xml_text)

        if feed.bozo:
            print("⚠ RSS解析警告:", feed.bozo_exception)

        return feed.entries

    except Exception as e:
        print("❌ RSS例外:", e)
        return []

# --------------------------------------------------
# 英語タイトル → 日本語タイトル生成
# --------------------------------------------------
def generate_japanese_title(en_title):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
以下の英語ニュースタイトルを、日本語ニュース記事の自然な表現に翻訳してください。
余計な説明・翻訳案・候補案・注意書きは禁止です。
タイトルのみ出力してください。

英語タイトル:
{en_title}
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    title = response.text.strip()
    title = title.replace("#", "").replace("**", "").strip()
    return title

# --------------------------------------------------
# Geminiで記事本文生成（Markdown）
# --------------------------------------------------
def generate_markdown(article):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
以下の情報をもとに、日本語でニュース記事を書いてください。

【重要ルール】
- Markdown形式で出力
- 見出しは「## 概要」「## 背景」「## 今後の見通し」または「## 推測」
- 「翻訳案」「候補案」「注意書き」「補足」は絶対に禁止

【出力フォーマット】

元記事URL: {article["link"]}

## 概要
（ニュースの要約）

## 背景
（背景説明。なければ空欄でOK）

## 今後の見通し
（予測できない場合はこの項目は出力しない）

## 推測
（今後ありえる影響を記述）

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

    text = response.text.strip()

    # 不要なタイトル（Gemini が勝手に生成した場合）を排除
    lines = text.split("\n")
    if lines[0].startswith("#"):
        lines = lines[1:]
    text = "\n".join(lines).lstrip()

    # 空白整形
    text = re.sub(r"\n{3,}", "\n\n
