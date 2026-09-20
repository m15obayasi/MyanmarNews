"""Publish source-grounded Myanmar-language guides for living in Japan."""

import argparse
import io
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from pypdf import PdfReader

import main as news


TOPIC_HISTORY_FILE = "japan_life_topics.json"
BLOG_ID_ENV = "HATENA_BLOG_ID_INFO"
DEFAULT_BLOG_ID = "myanmar-japan-info.hatenablog.com"
GUIDEBOOK_URL = "https://www.moj.go.jp/isa/content/930004717.pdf"
PORTAL_URL = "https://www.moj.go.jp/isa/support/portal/"

# Topics are deliberately curated. The generator may explain one of these topics,
# but it may not invent a different legal, medical, or administrative subject.
TOPICS: List[Dict[str, Any]] = [
    {
        "id": "residence-card-loss",
        "title_ja": "在留カードをなくしたとき",
        "keywords": ["在留カード", "紛失", "再交付", "警察", "14日"],
        "sources": [
            {"title": "紛失等による在留カードの再交付申請", "url": "https://www.moj.go.jp/isa/applications/procedures/nyuukokukanri10_00010.html"},
            {"title": "在留カードに関するQ&A", "url": "https://www.moj.go.jp/isa/publications/faq/newimmiact_4_q-and-a_page2.html"},
        ],
    },
    {
        "id": "moving-city-office",
        "title_ja": "引っ越したときの市区町村での手続",
        "keywords": ["引越し", "転出", "転入", "住所", "市区町村", "住民票"],
        "sources": [{"title": "外国人生活支援ポータルサイト", "url": PORTAL_URL}],
    },
    {
        "id": "health-insurance",
        "title_ja": "国民健康保険と会社の健康保険の基本",
        "keywords": ["健康保険", "国民健康保険", "社会保険", "医療費", "保険料"],
        "sources": [
            {"title": "国民健康保険の外国人被保険者について", "url": "https://www.mhlw.go.jp/stf/newpage_21895.html"},
            {"title": "外国人従業員を雇用したときの手続き", "url": "https://www.nenkin.go.jp/service/kounen/tekiyo/hihokensha1/gaikokujinkoyou.html"},
        ],
    },
    {
        "id": "public-pension",
        "title_ja": "日本の公的年金の基本",
        "keywords": ["年金", "国民年金", "厚生年金", "保険料", "脱退一時金"],
        "sources": [
            {"title": "外国人のみなさまへ 年金に関する情報", "url": "https://www.nenkin.go.jp/tokusetsu/forresidents.html"},
            {"title": "知っておきたい年金のはなし（外国語版）", "url": "https://www.nenkin.go.jp/service/learn/shitteokitai_gaikoku.html"},
        ],
    },
    {
        "id": "visit-hospital",
        "title_ja": "日本で病院に行くときの流れ",
        "keywords": ["医療", "病院", "診療所", "受診", "健康保険", "問診票"],
        "sources": [
            {"title": "外国人向け多言語説明資料", "url": "https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/kenkou_iryou/iryou/kokusai/setsumei-ml.html"},
            {"title": "国民健康保険の外国人被保険者について", "url": "https://www.mhlw.go.jp/stf/newpage_21895.html"},
        ],
    },
    {
        "id": "earthquake-preparation",
        "title_ja": "地震が起きる前と起きた直後にすること",
        "keywords": ["地震", "災害", "避難", "避難所", "備蓄", "防災"],
        "sources": [
            {"title": "外国人のための減災のポイント", "url": "https://www.bousai.go.jp/kyoiku/gensai/index.html"},
            {"title": "外国人への災害情報の発信について", "url": "https://www.bousai.go.jp/kyoiku/gaikoku/index.html"},
        ],
    },
    {
        "id": "emergency-numbers",
        "title_ja": "110番と119番の違いと伝え方",
        "keywords": ["110", "119", "警察", "救急車", "消防", "緊急"],
        "sources": [{"title": "外国人生活支援ポータルサイト", "url": PORTAL_URL}],
    },
    {
        "id": "garbage-rules",
        "title_ja": "ごみの分け方と出し方",
        "keywords": ["ごみ", "ゴミ", "分別", "収集", "市区町村", "粗大ごみ"],
        "sources": [{"title": "外国人生活支援ポータルサイト", "url": PORTAL_URL}],
    },
    {
        "id": "renting-home",
        "title_ja": "日本で賃貸住宅を借りるときの基本",
        "keywords": ["住宅", "賃貸", "家賃", "契約", "敷金", "保証人"],
        "sources": [{"title": "外国人生活支援ポータルサイト", "url": PORTAL_URL}],
    },
    {
        "id": "work-contract",
        "title_ja": "雇用契約書と労働条件通知書の見方",
        "keywords": ["雇用", "労働条件", "契約", "賃金", "労働時間", "休日"],
        "sources": [{"title": "外国人生活支援ポータルサイト", "url": PORTAL_URL}],
    },
    {
        "id": "salary-slip",
        "title_ja": "給与明細の見方",
        "keywords": ["給与", "賃金", "控除", "税金", "健康保険", "厚生年金"],
        "sources": [{"title": "外国人生活支援ポータルサイト", "url": PORTAL_URL}],
    },
    {
        "id": "resident-tax",
        "title_ja": "住民税とは何か",
        "keywords": ["税金", "住民税", "所得", "納税", "市区町村"],
        "sources": [{"title": "外国人生活支援ポータルサイト", "url": PORTAL_URL}],
    },
    {
        "id": "bank-account",
        "title_ja": "銀行口座を作るときの準備",
        "keywords": ["銀行", "口座", "在留カード", "住所", "本人確認"],
        "sources": [{"title": "外国人生活支援ポータルサイト", "url": PORTAL_URL}],
    },
    {
        "id": "mobile-phone",
        "title_ja": "携帯電話を契約するときの注意点",
        "keywords": ["携帯電話", "契約", "料金", "解約", "本人確認"],
        "sources": [{"title": "外国人生活支援ポータルサイト", "url": PORTAL_URL}],
    },
    {
        "id": "childbirth-childcare",
        "title_ja": "妊娠・出産・子育てで最初に知ること",
        "keywords": ["妊娠", "出産", "母子健康手帳", "子育て", "市区町村"],
        "sources": [{"title": "外国人生活支援ポータルサイト", "url": PORTAL_URL}],
    },
]


def load_history(path: str = TOPIC_HISTORY_FILE) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise RuntimeError(f"{path} must contain a JSON list")
    return data


def save_history(history: List[Dict[str, Any]], path: str = TOPIC_HISTORY_FILE) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(history, handle, ensure_ascii=False, indent=2)


def select_topic(history: List[Dict[str, Any]], requested_id: Optional[str] = None) -> Dict[str, Any]:
    used = {str(item.get("id", "")) for item in history}
    if requested_id:
        topic = next((item for item in TOPICS if item["id"] == requested_id), None)
        if not topic:
            raise RuntimeError(f"Unknown topic id: {requested_id}")
        if requested_id in used:
            raise RuntimeError(f"Topic has already been published: {requested_id}")
        return topic
    topic = next((item for item in TOPICS if item["id"] not in used), None)
    if not topic:
        raise RuntimeError("All curated Japan-life topics have already been published")
    return topic


def _pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _relevant_excerpt(text: str, keywords: List[str], limit: int = 8000) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= limit:
        return text
    chunks = [text[index:index + 2200] for index in range(0, len(text), 2000)]
    ranked = sorted(
        enumerate(chunks),
        key=lambda item: (sum(item[1].count(word) for word in keywords), -item[0]),
        reverse=True,
    )
    selected = sorted(ranked[:3], key=lambda item: item[0])
    excerpt = "\n\n".join(chunk for _, chunk in selected)
    return excerpt[:limit]


def fetch_source(source: Dict[str, str], keywords: List[str]) -> Dict[str, str]:
    response = requests.get(
        source["url"],
        timeout=45,
        headers={"User-Agent": "MyanmarNewsBot/1.0 (+https://github.com/m15obayasi/MyanmarNews)"},
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").lower()
    if "pdf" in content_type or source["url"].lower().endswith(".pdf"):
        text = _pdf_text(response.content)
    else:
        response.encoding = response.apparent_encoding or response.encoding
        text = news.article_html_to_text(response.text)
    excerpt = _relevant_excerpt(text, keywords)
    if len(excerpt) < 300:
        raise RuntimeError(f"Official source text was too short: {source['url']}")
    return {**source, "excerpt": excerpt}


def collect_sources(topic: Dict[str, Any]) -> List[Dict[str, str]]:
    sources = [{"title": "生活・就労ガイドブック（出入国在留管理庁）", "url": GUIDEBOOK_URL}]
    sources.extend(topic["sources"])
    collected: List[Dict[str, str]] = []
    seen_urls = set()
    for source in sources:
        if source["url"] in seen_urls:
            continue
        try:
            collected.append(fetch_source(source, topic["keywords"]))
            seen_urls.add(source["url"])
        except Exception as exc:
            logging.warning("Could not use official source %s: %s", source["url"], exc)
    if len(collected) < 2:
        raise RuntimeError(f"Only {len(collected)} usable official source(s); at least 2 are required")
    return collected


def build_prompt(topic: Dict[str, Any], sources: List[Dict[str, str]]) -> str:
    blocks = []
    for index, source in enumerate(sources, 1):
        blocks.append(f"[{index}] {source['title']}\nURL: {source['url']}\n{source['excerpt']}")
    today = datetime.now(timezone.utc).date().isoformat()
    return f"""
あなたは、日本で暮らすミャンマー人に生活情報を伝える慎重な編集者です。次のテーマについて、ミャンマー語の記事を書いてください。

テーマ: {topic['title_ja']}
確認基準日: {today}

編集ルール:
- 本文と見出しは自然で分かりやすいミャンマー語にする。
- 手続で必要になる重要な日本語は「在留カード（ざいりゅうカード）」のように、日本語表記と読み方を併記する。
- 下記の日本政府・公的機関の資料だけを根拠にし、資料にない期限、金額、対象者、必要書類を推測しない。
- 自治体や個人の状況で違う事項は、その違いと確認先を明記する。
- 医療・法律・税務の個別判断はせず、緊急時や判断が必要な場合は公的窓口や専門家への確認を促す。
- 原文の長い引用や翻訳転載はせず、自分の言葉で実用的に要約する。
- 1行目を記事タイトル、2行目以降をMarkdown本文にする。
- 本文は1200～2200字程度。最初に短い要点、その後に番号付きの手順、必要なもの、注意点、問い合わせ先の順で構成する。
- 末尾にミャンマー語の「အရင်းအမြစ်များ」見出しを置き、使用した全資料を資料名とMarkdownリンクで示す。
- 読者を不安にさせる煽りや、確認できない断定はしない。
- 資料がテーマを十分に説明していない場合は、記事を書かず `SKIP: 理由` の1行だけを返す。

公式資料:
{chr(10).join(blocks)}
""".strip()


def validate_article(title: str, body: str) -> None:
    if not re.search(r"[\u1000-\u109f]", title + body):
        raise RuntimeError("Generated article does not contain Myanmar text")
    if len(body) < 900:
        raise RuntimeError("Generated article is unexpectedly short")
    if "http" not in body:
        raise RuntimeError("Generated article does not cite official sources")


def diagnose() -> None:
    os.environ.setdefault(BLOG_ID_ENV, DEFAULT_BLOG_ID)
    for name in ("GEMINI_API_KEY", "HATENA_ID", "HATENA_API_KEY", BLOG_ID_ENV):
        if not os.getenv(name):
            raise RuntimeError(f"Missing environment variable: {name}")
    news.call_gemini_generate_content("မြန်မာဘာသာဖြင့် OK ဟုသာ ပြန်ဖြေပါ။")
    endpoint = f"https://blog.hatena.ne.jp/{os.environ['HATENA_ID']}/{os.environ[BLOG_ID_ENV]}/atom/entry"
    response = requests.get(endpoint, auth=(os.environ["HATENA_ID"], os.environ["HATENA_API_KEY"]), timeout=30)
    response.raise_for_status()
    logging.info("Diagnostics passed. No article posted.")


def run(dry_run: bool = False, topic_id: Optional[str] = None) -> Dict[str, Any]:
    os.environ.setdefault(BLOG_ID_ENV, DEFAULT_BLOG_ID)
    history = load_history()
    topic = select_topic(history, topic_id)
    logging.info("Selected Japan-life topic: %s", topic["title_ja"])
    sources = collect_sources(topic)
    output = news.call_gemini_generate_content(build_prompt(topic, sources))
    if output.lstrip().upper().startswith("SKIP:"):
        logging.warning("Publication skipped safely: %s", output[:500])
        return {"topic": topic, "article_url": None, "skipped": output[:500]}
    title, body = news.split_title_and_body_from_gemini(output)
    validate_article(title, body)
    if dry_run:
        logging.info("Dry run completed; no article was published and history was not changed")
        return {"topic": topic, "title": title, "body": body, "article_url": None}

    article_url = news.post_to_hatena(
        title,
        body,
        "",
        blog_id_env=BLOG_ID_ENV,
        categories=["ဂျပန်တွင် နေထိုင်မှု"],
    )
    record = {
        "id": topic["id"],
        "topic_ja": topic["title_ja"],
        "title": title,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "article_url": article_url,
        "sources": [{"title": item["title"], "url": item["url"]} for item in sources],
    }
    history.append(record)
    save_history(history)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Publish a Myanmar-language guide to living in Japan")
    parser.add_argument("--dry-run", action="store_true", help="Generate and validate without publishing")
    parser.add_argument("--diagnose", action="store_true", help="Check Gemini and Hatena without publishing")
    parser.add_argument("--topic", help="Use one unpublished curated topic id")
    arguments = parser.parse_args()
    if arguments.diagnose:
        diagnose()
    else:
        run(dry_run=arguments.dry_run, topic_id=arguments.topic)
