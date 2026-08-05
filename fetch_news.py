#!/usr/bin/env python3
"""
বাংলাদেশের পত্রিকার RSS ফিড থেকে প্রযুক্তি-সংক্রান্ত খবর খুঁজে বের করে
public/news.json ফাইলে জমা রাখে। GitHub Actions থেকে প্রতিদিন চলার জন্য বানানো।

কীভাবে "প্রযুক্তি খবর" চেনা হয় (হাইব্রিড পদ্ধতি):
  ১) খবরের লিংকে config.json-এর tech_path_hints-এর কোনো অংশ থাকলে ->
     সরাসরি "বিভাগ" হিসেবে ধরে নেওয়া হয়, কীওয়ার্ড লাগে না
  ২) তা না মিললে -> টাইটেল/বিবরণে কীওয়ার্ড খোঁজা হয়
"""
import difflib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

import feedparser

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
NEWS_PATH = ROOT / "public" / "news.json"


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        try:
            config = json.load(f)
        except json.JSONDecodeError as e:
            # config.json এডিট করার সময় প্রায়ই কমা/ব্র্যাকেট ভুল হয়ে যায় -> এখানেই
            # স্পষ্ট বাংলা বার্তা দিয়ে থামানো হচ্ছে, যাতে GitHub Actions log দেখেই
            # সমস্যাটা বোঝা যায়, পাইথনের জটিল ট্রেসব্যাক ঘাঁটতে না হয়।
            print(f"[এরর] config.json ফাইলটি সঠিক JSON ফরম্যাটে নেই: {e}", file=sys.stderr)
            print("[এরর] সম্ভবত কোথাও কমা (,) বাদ পড়েছে বা বেশি বসে গেছে। ফাইলটি একটা JSON validator (যেমন jsonlint.com) দিয়ে চেক করুন।", file=sys.stderr)
            sys.exit(1)

    required_keys = ["feeds", "keywords"]
    missing = [k for k in required_keys if k not in config]
    if missing:
        print(f"[এরর] config.json-এ এই প্রয়োজনীয় অংশগুলো নেই: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    return config


def load_existing_news():
    if NEWS_PATH.exists():
        with open(NEWS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


def parse_date(entry):
    """entry থেকে published/updated সময় বের করার চেষ্টা করে, না পেলে এখনকার সময় দেয়।"""
    for field in ("published", "updated"):
        raw = entry.get(field)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)


def matched_keywords(text, keywords):
    text_l = text.lower()
    return [kw for kw in keywords if kw.lower() in text_l]


def is_tech_by_path(link, path_hints):
    link_l = link.lower()
    return any(hint.lower() in link_l for hint in path_hints)


def classify_entry(entry, path_hints, keywords):
    """
    একটা এন্ট্রি প্রযুক্তি-সংক্রান্ত কিনা যাচাই করে।
    রিটার্ন করে: (matched: bool, matched_by: "বিভাগ"|"কীওয়ার্ড"|None, matched_keywords: list)
    """
    title = entry.get("title", "").strip()
    summary = entry.get("summary", "") or entry.get("description", "")
    link = entry.get("link", "")

    if is_tech_by_path(link, path_hints):
        return True, "বিভাগ", []

    hits = matched_keywords(f"{title} {summary}", keywords)
    if hits:
        return True, "কীওয়ার্ড", hits

    return False, None, []


def fetch_feed_items(feed_conf, path_hints, keywords):
    items = []
    parsed = feedparser.parse(feed_conf["url"])
    if parsed.bozo and not parsed.entries:
        print(f"  [সতর্কতা] ফিড পড়া যায়নি: {feed_conf['name']} ({parsed.bozo_exception})", file=sys.stderr)
        return items

    for entry in parsed.entries:
        matched, matched_by, hits = classify_entry(entry, path_hints, keywords)
        if not matched:
            continue

        items.append({
            "source": feed_conf["name"],
            "title": entry.get("title", "").strip(),
            "link": entry.get("link", ""),
            "published": parse_date(entry).isoformat(),
            "matched_by": matched_by,
            "matched_keywords": hits,
        })
    return items


def main():
    config = load_config()
    keywords = config["keywords"]
    path_hints = config.get("tech_path_hints", [])
    retain_days = config.get("retain_days", 14)
    max_items = config.get("max_items_per_run", 300)

    existing = load_existing_news()
    for item in existing:
        item.setdefault("also_from", [])
    existing_links = {item["link"] for item in existing}

    # গত ৩ দিনের মধ্যে প্রকাশিত আইটেমগুলোর সাথেই শুধু মিল খোঁজা হবে (পারফরম্যান্সের জন্য)
    dedup_cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    def recent_for_dedup(item):
        try:
            return datetime.fromisoformat(item["published"]) >= dedup_cutoff
        except Exception:
            return False

    accepted_new = []
    dedup_pool = [item for item in existing if recent_for_dedup(item)]

    def find_similar(title, pool):
        """একই খবর অন্য পত্রিকায় ভিন্নভাবে লেখা হলেও (৬০%+ শব্দ মিল) শনাক্ত করে।"""
        for candidate in pool:
            ratio = difflib.SequenceMatcher(None, title, candidate["title"]).ratio()
            if ratio >= 0.6:
                return candidate
        return None

    for feed_conf in config["feeds"]:
        print(f"ফিড দেখা হচ্ছে: {feed_conf['name']}")
        found = fetch_feed_items(feed_conf, path_hints, keywords)
        for item in found:
            if item["link"] in existing_links:
                continue
            twin = find_similar(item["title"], dedup_pool)
            if twin is not None:
                # একই খবর অন্য পত্রিকা থেকে আগেই এসেছে -> নতুন এন্ট্রি না বানিয়ে সোর্স যোগ করা
                if item["source"] not in twin["also_from"] and item["source"] != twin["source"]:
                    twin["also_from"].append(item["source"])
                existing_links.add(item["link"])
                continue
            item["also_from"] = []
            accepted_new.append(item)
            dedup_pool.append(item)
            existing_links.add(item["link"])

    print(f"নতুন প্রযুক্তি-সংক্রান্ত খবর পাওয়া গেছে: {len(accepted_new)}টি")

    combined = existing + accepted_new

    cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)
    def is_recent(item):
        try:
            return datetime.fromisoformat(item["published"]) >= cutoff
        except Exception:
            return True
    combined = [item for item in combined if is_recent(item)]

    combined.sort(key=lambda x: x["published"], reverse=True)
    combined = combined[:max_items]

    NEWS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(NEWS_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "keywords": keywords,
            "items": combined,
        }, f, ensure_ascii=False, indent=2)

    print(f"মোট {len(combined)}টি খবর public/news.json-এ সংরক্ষিত হলো।")


if __name__ == "__main__":
    main()
