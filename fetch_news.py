"""Collects news for ~60 countries, finds a picture for every story, writes news.json.
Only the Python standard library is used, so nothing needs installing."""
import json, os, re, html, time, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

MAX_PER_COUNTRY = 5
BREAKING_HOURS = 3
MAX_AI_TEXT = 40          # free Gemini calls per run
MAX_OG_FETCH = 80         # article-page picture lookups per run
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
IMG_EXT = re.compile(r"\.(jpe?g|png|webp|gif)(\?|$)", re.I)
ATOM = "{http://www.w3.org/2005/Atom}"
counters = {"ai": 0, "og": 0}

def clean(t):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t or ""))).strip()

def local(tag):
    return tag.rsplit("}", 1)[-1]

def to_dt(s):
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        except Exception:
            return datetime.now(timezone.utc)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

def fetch(url, limit=None):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(limit) if limit else r.read()

def parse(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for it in list(root.iter("item")) + list(root.iter(ATOM + "entry")):
        d = {"title": "", "link": "", "desc": "", "date": None, "image": "", "source": ""}
        raw_desc = ""
        for ch in it:
            n, txt = local(ch.tag), (ch.text or "").strip()
            if n == "title": d["title"] = clean(txt)
            elif n == "link": d["link"] = txt or ch.get("href", "")
            elif n in ("description", "summary"): raw_desc = raw_desc or txt
            elif n in ("pubDate", "updated", "published"): d["date"] = to_dt(txt)
            elif n in ("thumbnail", "content") and ch.get("url"):
                u, ty = ch.get("url"), ch.get("type", "") + ch.get("medium", "")
                if "image" in ty or IMG_EXT.search(u) or n == "thumbnail":
                    d["image"] = d["image"] or u
            elif n == "enclosure" and ch.get("url") and ("image" in ch.get("type", "") or IMG_EXT.search(ch.get("url"))):
                d["image"] = d["image"] or ch.get("url")
            elif n == "Image" and txt: d["image"] = d["image"] or txt      # Bing News
            elif n in ("Source", "source") and txt: d["source"] = txt
        if not d["image"]:
            m = re.search(r'<img[^>]+src=["\']([^"\']+)', html.unescape(raw_desc))
            if m: d["image"] = m.group(1)
        d["desc"] = clean(raw_desc)
        d["date"] = d["date"] or datetime.now(timezone.utc)
        # Bing wraps the real address in a "url=" parameter
        if "bing.com/news/apiclick" in d["link"]:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(d["link"]).query).get("url")
            if q: d["link"] = q[0]
        if d["source"] and d["title"].endswith(" - " + d["source"]):
            d["title"] = d["title"][: -len(d["source"]) - 3]
        if not d["source"]:
            d["source"] = urllib.parse.urlparse(d["link"]).netloc.replace("www.", "")
        out.append(d)
    return out

def og_image(url):
    if counters["og"] >= MAX_OG_FETCH or "news.google.com" in url or "bing.com" in url:
        return ""
    counters["og"] += 1
    try:
        page = fetch(url, 250000).decode("utf-8", "ignore")
        for pat in (r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']'):
            m = re.search(pat, page, re.I)
            if m: return urllib.parse.urljoin(url, html.unescape(m.group(1)))
    except Exception:
        pass
    return ""

def ai(title, text):
    if not GEMINI_KEY or counters["ai"] >= MAX_AI_TEXT:
        return None
    counters["ai"] += 1
    prompt = ("You write for a news site. Using ONLY the facts below, reply with JSON: "
              '{"summary":"2 neutral sentences","image_prompt":"short description of an editorial illustration, no text, no real people"}\n'
              f"Headline: {title}\nText: {text}")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"responseMimeType": "application/json"}}).encode()
    try:
        req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(json.load(r)["candidates"][0]["content"]["parts"][0]["text"])
        return j["summary"], j["image_prompt"]
    except Exception as e:
        print("  AI skipped:", e)
        return None

def ai_image(prompt):
    p = urllib.parse.quote("editorial news illustration, " + prompt[:200] + ", no text")
    return f"https://image.pollinations.ai/prompt/{p}?width=800&height=450&nologo=true"

def flag(code):
    return "".join(chr(127397 + ord(c)) for c in code)

def colors(code):
    h = sum(ord(c) * 37 for c in code) % 360
    return [f"hsl({h},55%,26%)", f"hsl({(h + 35) % 360},60%,45%)"]

def get_items(country, extra):
    items = []
    sources = [(n, u) for n, u in extra.get(country, [])]
    q = urllib.parse.quote(country)
    sources.append(("Bing", f"https://www.bing.com/news/search?q={q}&format=rss&setlang=en"))
    for i, (name, url) in enumerate(sources):
        try:
            items += parse(fetch(url))
        except Exception as e:
            print(f"  feed failed ({country} / {name}): {e}")
        time.sleep(0.4)
    if len(items) < 3:   # backup source
        try:
            items += parse(fetch(f"https://news.google.com/rss/search?q={q}+when:1d&hl=en&gl=US&ceid=US:en"))
        except Exception as e:
            print(f"  google backup failed ({country}): {e}")
    return items

def main():
    cache = {}
    if os.path.exists("news.json"):
        try:
            cache = {s["url"]: s for s in json.load(open("news.json", encoding="utf-8"))["stories"]}
        except Exception:
            pass
    countries = json.load(open("countries.json", encoding="utf-8"))
    extra = json.load(open("feeds.json", encoding="utf-8"))
    now = datetime.now(timezone.utc)
    stories = []
    for country, code in countries.items():
        print("Country:", country)
        seen, picked = set(), []
        for d in sorted(get_items(country, extra), key=lambda x: x["date"], reverse=True):
            key = d["title"].lower()
            if d["title"] and d["link"] and key not in seen and (now - d["date"]).days < 3:
                seen.add(key); picked.append(d)
            if len(picked) == MAX_PER_COUNTRY: break
        for d in picked:
            old = cache.get(d["link"])
            if old:
                summary, img, kind = old["summary"], old["image"], old.get("imageType", "ai")
            else:
                res = ai(d["title"], d["desc"])
                summary = res[0] if res else (d["desc"][:220] + ("…" if len(d["desc"]) > 220 else ""))
                real = d["image"] or og_image(d["link"])
                if real: img, kind = real, "photo"
                else: img, kind = ai_image(res[1] if res else d["title"]), "ai"
            stories.append({"country": country, "code": code, "flag": flag(code), "c": colors(code), "emoji": "📰",
                            "title": d["title"], "summary": summary or d["title"], "image": img, "imageType": kind,
                            "topic": "News", "published": d["date"].isoformat(),
                            "breaking": (now - d["date"]).total_seconds() < BREAKING_HOURS * 3600,
                            "url": d["link"], "refs": [[d["source"], d["link"]]]})
    if not stories:
        print("No stories found; keeping the old news.json")
        return
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump({"updated": now.isoformat(), "stories": stories}, f, ensure_ascii=False, indent=1)
    print("Saved", len(stories), "stories")

if __name__ == "__main__":
    main()
