"""Reads RSS feeds per country, optionally adds an AI summary + image idea, writes news.json.
Uses only the Python standard library, so there is nothing to install."""
import json, os, re, html, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

MAX_PER_COUNTRY = 5
BREAKING_HOURS = 3
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")  # check Google AI Studio for the current free model name
UA = {"User-Agent": "Mozilla/5.0 (TodayNewsBot)"}

def clean(t):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t or ""))).strip()

def to_dt(s):
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return datetime.now(timezone.utc)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

def parse(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.iter("item"):
        out.append((clean(it.findtext("title")), (it.findtext("link") or "").strip(),
                    clean(it.findtext("description")), to_dt(it.findtext("pubDate") or "")))
    for en in root.iter("{http://www.w3.org/2005/Atom}entry"):
        ns = "{http://www.w3.org/2005/Atom}"
        link = en.find(ns + "link")
        out.append((clean(en.findtext(ns + "title")), link.get("href") if link is not None else "",
                    clean(en.findtext(ns + "summary")), to_dt(en.findtext(ns + "updated") or "")))
    return out

def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()

def ai(title, text):
    """Free Gemini call. Returns (summary, image_prompt) or None."""
    if not GEMINI_KEY:
        return None
    prompt = ("You write for a news site. Using ONLY the facts below, reply with JSON: "
              '{"summary":"2 neutral sentences","image_prompt":"a short description of an editorial illustration, no text, no real people"}\n'
              f"Headline: {title}\nText: {text}")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"responseMimeType": "application/json"}}).encode()
    try:
        req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        j = json.loads(data["candidates"][0]["content"]["parts"][0]["text"])
        return j["summary"], j["image_prompt"]
    except Exception as e:
        print("  AI skipped:", e)
        return None

def image_url(prompt):
    p = urllib.parse.quote("editorial illustration, " + prompt + ", no text")
    return f"https://image.pollinations.ai/prompt/{p}?width=800&height=450&nologo=true"

def main():
    cache = {}
    if os.path.exists("news.json"):
        try:
            cache = {s["url"]: s for s in json.load(open("news.json", encoding="utf-8"))["stories"]}
        except Exception:
            pass
    now = datetime.now(timezone.utc)
    stories = []
    for c in json.load(open("feeds.json", encoding="utf-8")):
        items = []
        for source, url in c["feeds"]:
            try:
                items += [(t, l, d, p, source) for t, l, d, p in parse(fetch(url))]
            except Exception as e:
                print(f"Feed failed ({source}): {e}")
        seen, picked = set(), []
        for t, l, d, p, source in sorted(items, key=lambda x: x[3], reverse=True):
            if t and l and t.lower() not in seen:
                seen.add(t.lower()); picked.append((t, l, d, p, source))
            if len(picked) == MAX_PER_COUNTRY:
                break
        for t, l, d, p, source in picked:
            old = cache.get(l)
            if old:  # reuse earlier AI work, saves your free quota
                summary, img = old["summary"], old["image"]
            else:
                res = ai(t, d)
                summary = res[0] if res else (d[:220] + ("…" if len(d) > 220 else ""))
                img = image_url(res[1] if res else t)
            stories.append({"country": c["name"], "flag": c["flag"], "c": c["colors"], "emoji": "📰",
                            "title": t, "summary": summary, "image": img, "topic": "News",
                            "published": p.isoformat(),
                            "breaking": (now - p).total_seconds() < BREAKING_HOURS * 3600,
                            "url": l, "refs": [[source, l]]})
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump({"updated": now.isoformat(), "stories": stories}, f, ensure_ascii=False, indent=1)
    print("Saved", len(stories), "stories")

if __name__ == "__main__":
    main()
