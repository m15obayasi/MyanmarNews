"""Create one source-grounded Japanese Myanmar explainer per day."""

import argparse
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import requests

import main as news


TOPIC_HISTORY_FILE = "explainer_topics.json"
GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"

# These are deliberately bounded.  The model chooses among unused editorially safe
# candidates instead of inventing a potentially duplicative or unsuitable topic.
TOPICS: List[Dict[str, str]] = [
    {"id": "pdf-basics", "title": "ミャンマーのPDFとは？", "query": "People's Defence Force PDF resistance"},
    {"id": "nug-basics", "title": "ミャンマーのNUGとは？", "query": "National Unity Government NUG"},
    {"id": "aa-basics", "title": "アラカン軍（AA）とは？", "query": "Arakan Army AA Rakhine"},
    {"id": "eao-basics", "title": "ミャンマーの少数民族武装組織（EAO）とは？", "query": "ethnic armed organizations EAO"},
    {"id": "pdf-vs-eao", "title": "PDFと少数民族武装組織（EAO）の違いは？", "query": "PDF EAO differences resistance"},
    {"id": "nug-vs-sac", "title": "NUGと軍政当局（SAC）の違いは？", "query": "NUG State Administration Council SAC"},
    {"id": "rakhine-importance", "title": "なぜラカイン州は重要なのか？", "query": "Rakhine State strategic importance"},
    {"id": "sagaing-importance", "title": "なぜザガイン地方域は重要なのか？", "query": "Sagaing Region resistance importance"},
    {"id": "shan-importance", "title": "なぜシャン州は重要なのか？", "query": "Shan State conflict border trade"},
    {"id": "myawaddy-importance", "title": "なぜミャワディは重要なのか？", "query": "Myawaddy Thailand border trade"},
    {"id": "china-relations", "title": "なぜ中国はミャンマーを重視するのか？", "query": "China Myanmar relations pipelines border"},
    {"id": "thailand-relations", "title": "ミャンマーとタイの関係とは？", "query": "Thailand Myanmar relations refugees trade"},
    {"id": "asean-role", "title": "ASEANはミャンマー問題で何をしている？", "query": "ASEAN Myanmar Five-Point Consensus"},
    {"id": "conscription", "title": "ミャンマーの徴兵制度とは？", "query": "Myanmar conscription law military service"},
    {"id": "airstrikes", "title": "なぜミャンマー内戦で空爆が重要なのか？", "query": "Myanmar airstrikes civilians conflict"},
    {"id": "rare-earths", "title": "ミャンマーのレアアースはなぜ重要？", "query": "Myanmar rare earth mining China"},
    {"id": "kyat-economy", "title": "ミャンマー・チャットの下落は暮らしにどう影響する？", "query": "Myanmar kyat inflation economy"},
    {"id": "power-cuts", "title": "ミャンマーの停電はなぜ続く？", "query": "Myanmar electricity power cuts"},
    {"id": "rohingya-basics", "title": "ロヒンギャ問題とは？", "query": "Rohingya crisis Myanmar Rakhine"},
    {"id": "displacement", "title": "ミャンマーの国内避難民とは？", "query": "Myanmar internally displaced people humanitarian"},
    {"id": "japan-aid", "title": "日本のミャンマー支援は現在どうなっている？", "query": "Japan aid Myanmar ODA humanitarian"},
    {"id": "japan-migrants", "title": "日本でミャンマー人が増えているのはなぜ？", "query": "Myanmar migrants workers Japan"},
    {"id": "online-scams", "title": "ミャンマー国境地帯の特殊詐欺拠点とは？", "query": "Myanmar scam centres border compounds"},
    {"id": "federal-democracy", "title": "ミャンマーで語られる連邦民主制とは？", "query": "Myanmar federal democracy ethnic states"},
]

TRUSTED_DOMAINS = {
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "un.org", "ohchr.org",
    "reliefweb.int", "crisisgroup.org", "hrw.org", "amnesty.org", "usip.org",
    "worldbank.org", "ilo.org", "unhcr.org", "wfp.org", "asean.org",
    "english.dvb.no", "myanmar-now.org", "irrawaddy.com", "mizzima.com",
    "thediplomat.com", "frontiermyanmar.net",
}


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


def unused_topics(history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    used = {str(item.get("id", "")) for item in history}
    return [topic for topic in TOPICS if topic["id"] not in used]


def recent_news_titles(limit: int = 30) -> List[str]:
    titles: List[str] = []
    for source in news.RSS_SOURCES:
        try:
            for entry in news.fetch_rss_entries(source):
                title = str(getattr(entry, "title", "")).strip()
                if title and title not in titles:
                    titles.append(title)
                if len(titles) >= limit:
                    return titles
        except Exception as exc:
            logging.warning("Could not use %s for topic signals: %s", source["name"], exc)
    return titles


def parse_json_object(text: str) -> Dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Model response did not contain a JSON object")
    return json.loads(cleaned[start:end + 1])


def select_topic(history: List[Dict[str, Any]], signals: Optional[List[str]] = None) -> Dict[str, str]:
    candidates = unused_topics(history)
    if not candidates:
        raise RuntimeError("All curated explainer topics have already been published")
    signals = signals if signals is not None else recent_news_titles()
    prompt = f"""
あなたは日本語のミャンマー専門メディアの編集者です。未掲載候補から今日の解説記事を1件だけ選んでください。
速報性だけでなく、日本の一般読者がニュースを理解するための基礎価値、政治・軍事以外とのバランス、直近ニュースとの関連を考慮してください。
候補にないテーマは作らないでください。JSONだけを返してください: {{"id":"候補ID","reason":"日本語で短い理由"}}

未掲載候補:
{json.dumps(candidates, ensure_ascii=False)}

直近ニュース見出し（事実認定には使わず、関心の手掛かりだけにする）:
{json.dumps(signals[:30], ensure_ascii=False)}
""".strip()
    try:
        choice = parse_json_object(news.call_gemini_generate_content(prompt))
        selected_id = str(choice.get("id", ""))
        selected = next((topic for topic in candidates if topic["id"] == selected_id), None)
        if selected:
            return selected
        raise ValueError(f"Unknown topic id: {selected_id}")
    except Exception as exc:
        logging.warning("Topic selection model failed; using first unused curated topic: %s", exc)
        return candidates[0]


def domain_is_trusted(domain: str) -> bool:
    domain = domain.lower().removeprefix("www.")
    return any(domain == allowed or domain.endswith("." + allowed) for allowed in TRUSTED_DOMAINS)


def research_terms(query: str) -> List[str]:
    ignored = {"myanmar", "what", "why", "with", "from", "into", "state"}
    terms = {word for word in re.findall(r"[a-z0-9]+", query.lower()) if len(word) >= 3 and word not in ignored}
    if "defence" in terms:
        terms.add("defense")
    if "defense" in terms:
        terms.add("defence")
    return sorted(terms)


def gdelt_candidates(query: str, max_records: int = 50) -> List[Dict[str, Any]]:
    params = {
        "query": f"Myanmar {query}",
        "mode": "ArtList",
        "maxrecords": str(max_records),
        "format": "json",
        "timespan": "1year",
        "sort": "DateDesc",
    }
    response = requests.get(
        GDELT_ENDPOINT,
        params=params,
        timeout=45,
        headers={"User-Agent": "MyanmarNewsBot/1.0 (+https://github.com/m15obayasi/MyanmarNews)"},
    )
    response.raise_for_status()
    return response.json().get("articles", [])


def fetch_research_sources(topic: Dict[str, str], limit: int = 5) -> List[Dict[str, str]]:
    """Return direct URLs and usable excerpts from distinct trusted publishers."""
    results: List[Dict[str, str]] = []
    used_domains = set()
    encoded_query = quote_plus(topic["query"])
    search_feeds = [
        ("english.dvb.no", f"https://english.dvb.no/?s={encoded_query}&feed=rss2"),
        ("myanmar-now.org", f"https://myanmar-now.org/en/?s={encoded_query}&feed=rss2"),
        ("reliefweb.int", f"https://reliefweb.int/updates/rss.xml?search=Myanmar%20{encoded_query}"),
    ]
    terms = research_terms(topic["query"])
    for domain, feed_url in search_feeds:
        try:
            entries = news.fetch_rss_entries({"name": f"research:{domain}", "url": feed_url})
        except Exception as exc:
            logging.warning("Research feed failed for %s: %s", domain, exc)
            continue
        ranked = []
        for entry in entries:
            text = news.get_entry_article_text(entry)
            if len(text.split()) < 120:
                continue
            haystack = (str(getattr(entry, "title", "")) + "\n" + text[:5000]).lower()
            matched = {term for term in terms if re.search(rf"\b{re.escape(term)}\b", haystack)}
            if len(matched) < min(2, len(terms)):
                continue
            ranked.append((len(matched), entry, text))
        if ranked:
            _, entry, text = max(ranked, key=lambda item: item[0])
            results.append({
                "publisher": domain,
                "title": str(getattr(entry, "title", "")).strip(),
                "url": str(getattr(entry, "link", "")),
                "date": str(getattr(entry, "published", "")),
                "excerpt": text[:5000],
            })
            used_domains.add(domain)
        if len(results) >= limit:
            break

    try:
        candidates = gdelt_candidates(topic["query"]) if len(results) < limit else []
    except Exception as exc:
        logging.warning("GDELT research index unavailable; continuing with publisher feeds: %s", exc)
        candidates = []

    for item in candidates:
        domain = str(item.get("domain", "")).lower().removeprefix("www.")
        url = str(item.get("url", ""))
        if not url.startswith("http") or not domain_is_trusted(domain) or domain in used_domains:
            continue
        page = news.fetch_article_html(url)
        if not page:
            continue
        text = news.article_html_to_text(page)
        if len(text.split()) < 120:
            continue
        results.append({
            "publisher": domain,
            "title": str(item.get("title", "")).strip(),
            "url": url,
            "date": str(item.get("seendate", "")),
            "excerpt": text[:5000],
        })
        used_domains.add(domain)
        if len(results) >= limit:
            break
    if len(results) < 2:
        raise RuntimeError(
            f"Only {len(results)} usable trusted source(s) found for {topic['id']}; at least 2 are required"
        )
    return results


def build_explainer_prompt(topic: Dict[str, str], sources: List[Dict[str, str]]) -> str:
    source_blocks = []
    for index, source in enumerate(sources, 1):
        source_blocks.append(
            f"[{index}] {source['publisher']} | {source['title']} | {source['url']}\n{source['excerpt']}"
        )
    today = datetime.now(timezone.utc).date().isoformat()
    return f"""
あなたは慎重な日本語のミャンマー情勢解説者です。次のテーマについて、日本語の解説記事を書いてください。

テーマ: {topic['title']}
基準日: {today}

重要な編集ルール:
- 下記資料だけを根拠にし、資料にない人数・日付・支配地域・因果関係を補わない。
- 現在進行中の政治・軍事情勢は「～と報じられている」「～は～と主張している」など帰属を明示し、断定しすぎない。
- 独立系メディア、当事者、国際機関の記述を区別し、食い違いがあれば併記する。
- 2つ以上の異なる情報源を本文中でMarkdownリンクとして示す。単一情報源だけで重要な主張を確定しない。
- 原文の長い引用や翻訳転載はせず、自分の言葉で要約する。
- 日本の一般読者向けに略語と背景を説明する。
- 1行目をタイトル、2行目以降をMarkdown本文とする。
- 本文はおおむね1800～3000字。「要点」「背景」「現在の論点」「日本から見る際の注意点」「まとめ」の見出しを使う。
- 末尾に「参考資料」見出しを置き、使用した全資料を媒体名・記事名・URLの箇条書きで示す。
- SEOを意識して自然なタイトルにするが、煽り・勝敗の断定・誇張は避ける。
- 資料がテーマを十分に説明していない、または相互確認に不足する場合は記事を書かず、`SKIP: 資料不足の理由` の1行だけを返す。

資料:
{chr(10).join(source_blocks)}
""".strip()


def run(dry_run: bool = False) -> Dict[str, Any]:
    history = load_history()
    topic = select_topic(history)
    logging.info("Selected explainer topic: %s", topic["title"])
    sources = fetch_research_sources(topic)
    output = news.call_gemini_generate_content(build_explainer_prompt(topic, sources))
    if output.lstrip().upper().startswith("SKIP:"):
        raise RuntimeError("Gemini declined publication because the collected sources were insufficient: " + output[:300])
    title, body = news.split_title_and_body_from_gemini(output)
    if len(body) < 800:
        raise RuntimeError("Generated explainer is unexpectedly short")

    if dry_run:
        logging.info("Dry run completed; no article was published and history was not changed")
        return {"topic": topic, "title": title, "sources": sources, "article_url": None}

    article_url = news.post_to_hatena(
        title,
        body,
        "",
        blog_id_env="HATENA_BLOG_ID",
        categories=["解説", "ミャンマー"],
    )
    record = {
        "id": topic["id"],
        "topic": topic["title"],
        "published_at": datetime.now(timezone.utc).isoformat(),
        "article_url": article_url,
        "sources": [{key: item[key] for key in ("publisher", "title", "url")} for item in sources],
    }
    history.append(record)
    save_history(history)
    if article_url:
        news.post_to_x_if_configured(title, article_url)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Publish a daily Japanese Myanmar explainer")
    parser.add_argument("--dry-run", action="store_true", help="Generate and validate without publishing")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
