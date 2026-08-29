import os
import re
import json
import time
import hashlib
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from ddgs import DDGS

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID", "634834787")
SEEN_FILE = BASE_DIR / "seen_jobs.json"

if not BOT_TOKEN:
    raise SystemExit("ERROR: BOT_TOKEN is missing in .env")

# =========================
# YOUR JOB PROFILE
# =========================
TARGET_LOCATIONS = [
    "gift city", "gandhinagar", "ahmedabad", "gujarat"
]

REMOTE_TERMS = ["remote", "work from home", "india"]

TRADING_TERMS = [
    "derivatives", "derivative", "equity derivatives",
    "f&o", "fno", "options", "futures",
    "options trader", "futures trader",
    "derivatives trader", "derivatives analyst",
    "trading analyst", "trader", "trading desk",
    "quant trader", "quantitative analyst",
    "quant research", "algo trading",
    "algorithmic trading", "execution trader",
    "capital markets", "market risk"
]

SKILL_TERMS = [
    "options", "futures", "f&o", "derivatives",
    "python", "excel", "algo trading",
    "algorithmic trading", "quant",
    "capital markets", "market risk",
    "risk management", "equity trading"
]

EXPERIENCE_PATTERNS = [
    r"\b3\s*[-–]\s*5\s*(?:years?|yrs?)\b",
    r"\b4\s*[-–]\s*6\s*(?:years?|yrs?)\b",
    r"\b4\s*(?:\+|plus)\s*(?:years?|yrs?)\b",
    r"\b3\s*(?:\+|plus)\s*(?:years?|yrs?)\b",
    r"\b4\s*(?:years?|yrs?)\b",
    r"\b5\s*(?:years?|yrs?)\b",
    r"\b3\s*(?:years?|yrs?)\b",
]

# Broad queries. DDGS is much more reliable with these than complex site: queries.
SEARCH_QUERIES = [
    "derivatives jobs Ahmedabad",
    "derivatives jobs GIFT City",
    "derivatives jobs Gandhinagar",
    "equity derivatives jobs Ahmedabad",
    "equity derivatives jobs GIFT City",
    "options trader Ahmedabad",
    "options trader GIFT City",
    "futures trader Ahmedabad",
    "futures trader GIFT City",
    "trading analyst Ahmedabad",
    "trading analyst GIFT City",
    "trader jobs Ahmedabad finance",
    "quant trading jobs Ahmedabad",
    "quant jobs GIFT City",
    "capital markets jobs Ahmedabad",
    "capital markets jobs GIFT City",
    "F&O jobs Ahmedabad",
    "F&O jobs GIFT City",
    "market risk jobs GIFT City",
    "algo trading jobs Ahmedabad",
]

# Extra targeted searches for common job sites.
SITE_QUERIES = [
    "LinkedIn derivatives jobs Ahmedabad",
    "Naukri derivatives jobs Ahmedabad",
    "Indeed derivatives jobs Ahmedabad",
    "Foundit derivatives jobs Ahmedabad",
    "Glassdoor derivatives jobs Ahmedabad",
    "LinkedIn derivatives jobs GIFT City",
    "Naukri derivatives jobs GIFT City",
    "Indeed derivatives jobs GIFT City",
    "Foundit derivatives jobs GIFT City",
]

MAX_RESULTS_PER_QUERY = 10
REQUEST_TIMEOUT = 20


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def load_seen():
    if not SEEN_FILE.exists():
        return set()
    try:
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_seen(seen):
    SEEN_FILE.write_text(
        json.dumps(list(seen)[-10000:], indent=2),
        encoding="utf-8"
    )


def search_all():
    queries = SEARCH_QUERIES + SITE_QUERIES
    raw = []

    print(f"Running {len(queries)} job searches...")

    with DDGS() as ddgs:
        for i, query in enumerate(queries, 1):
            try:
                rows = ddgs.text(query, max_results=MAX_RESULTS_PER_QUERY)
                count = 0

                for row in rows:
                    title = clean(row.get("title"))
                    url = row.get("href") or row.get("url") or ""
                    body = clean(row.get("body") or row.get("snippet"))

                    if title and url:
                        raw.append({
                            "title": title,
                            "url": url,
                            "body": body,
                            "source": urlparse(url).netloc
                        })
                        count += 1

                print(f"[{i}/{len(queries)}] {query} -> {count} results")

            except Exception as exc:
                print(f"[warning] {query} -> {exc}")

    # URL deduplication
    unique = {}
    for job in raw:
        key = job["url"].split("#")[0].rstrip("/")
        unique[key] = job

    return list(unique.values())


def location_score(text):
    t = text.lower()

    if "gift city" in t:
        return 40
    if "gandhinagar" in t:
        return 30
    if "ahmedabad" in t:
        return 25
    if "gujarat" in t:
        return 15
    if any(x in t for x in REMOTE_TERMS):
        return 10

    return 0


def experience_score(text):
    t = text.lower()

    for pattern in EXPERIENCE_PATTERNS:
        if re.search(pattern, t):
            return 20

    # If no experience is stated, don't automatically reject it.
    # It can still be a relevant job.
    if "experience" not in t:
        return 5

    return 0


def score_job(job):
    text = f"{job['title']} {job['body']} {job['url']}".lower()

    trading_hits = sum(1 for x in TRADING_TERMS if x in text)
    skill_hits = sum(1 for x in SKILL_TERMS if x in text)

    score = 0
    score += min(trading_hits * 8, 40)
    score += min(skill_hits * 4, 20)
    score += location_score(text)
    score += experience_score(text)

    if "derivatives" in text:
        score += 8
    if "options" in text or "futures" in text:
        score += 5

    return min(score, 100)


def is_relevant(job):
    text = f"{job['title']} {job['body']} {job['url']}".lower()

    has_trading = any(term in text for term in TRADING_TERMS)
    has_target_location = (
        any(term in text for term in TARGET_LOCATIONS)
        or any(term in text for term in REMOTE_TERMS)
    )

    return has_trading and has_target_location


def make_key(job):
    raw = (
        clean(job["title"]).lower()
        + "|"
        + job["url"].split("?")[0].lower()
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": text,
            "disable_web_page_preview": False,
        },
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()
    return response.json()


def format_message(job, score):
    text = job["body"]

    if len(text) > 600:
        text = text[:600] + "..."

    return (
        "🔔 NEW TRADING JOB MATCH\n\n"
        f"💼 {job['title']}\n"
        f"🏢 {job['source']}\n"
        f"📍 Ahmedabad / GIFT City priority\n"
        f"🎯 Experience: around 4 years\n"
        f"⭐ Match Score: {score}%\n\n"
        f"📝 {text}\n\n"
        f"🔗 APPLY / VIEW JOB\n{job['url']}"
    )


def main():
    print("\n======================================")
    print(" Trading & Derivatives Job Alert")
    print(" Ahmedabad / GIFT City")
    print(" Target experience: ~4 years")
    print("======================================\n")

    seen = load_seen()
    jobs = search_all()

    print(f"\nUnique results collected: {len(jobs)}")

    matches = []

    for job in jobs:
        if not is_relevant(job):
            continue

        score = score_job(job)

        # Keep only reasonably strong matches.
        if score < 45:
            continue

        key = make_key(job)

        if key in seen:
            continue

        matches.append((score, key, job))

    matches.sort(key=lambda item: item[0], reverse=True)

    print(f"New relevant jobs: {len(matches)}")

    if not matches:
        print("No new strong matches found this run.")
        save_seen(seen)
        return

    sent = 0

    for score, key, job in matches[:10]:
        try:
            send_telegram(format_message(job, score))
            seen.add(key)
            sent += 1
            print(f"[TELEGRAM] Sent: {job['title']} ({score}%)")
            time.sleep(1)
        except Exception as exc:
            print(f"[Telegram error] {exc}")

    save_seen(seen)
    print(f"\nDone. Sent {sent} job alerts.")


if __name__ == "__main__":
    main()
