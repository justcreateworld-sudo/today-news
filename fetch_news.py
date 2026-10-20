"""Collects news for ~60 countries, finds a picture for every story, writes news.json.
Only the Python standard library is used, so nothing needs installing."""
import json, os, re, html, time, base64, urllib.request, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

MAX_PER_COUNTRY = 5
BREAKING_HOURS = 3
MAX_AI_TEXT = 40          # free Gemini calls per run
MAX_OG_FETCH = 1500
CAND = 12                 # candidates per country; stories with real photos are preferred         # article-page picture lookups per run
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
MODELS = [m for m in [os.environ.get("GEMINI_MODEL"), "gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"] if m]
_good = {"m": None}
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
IMG_EXT = re.compile(r"\.(jpe?g|png|webp|gif)(\?|$)", re.I)
ATOM = "{http://www.w3.org/2005/Atom}"
counters = {"ai": 0, "og": 0, "tr": 0, "gn": 0}
MAX_GN = 800              # Google News links turned into the publisher's own link per run
NONLATIN = {"si","ta","hi","ur","bn","ne","ar","fa","ps","he","ru","uk","zh","ja","ko","th","km","lo","mn","my","el","ka","hy","kk","be","sr","dv","pa","am"}
MAX_TR_CALLS = int(os.environ.get("MAX_TR_CALLS", "15"))   # free Gemini translation calls per run
HOME_LANGS = []                                  # languages shown on the first page
LANGS = {"en":("English","English"),"si":("සිංහල","Sinhala"),"ta":("தமிழ்","Tamil"),"hi":("हिन्दी","Hindi"),"ur":("اردو","Urdu"),"pa":("ਪੰਜਾਬੀ","Punjabi"),"bn":("বাংলা","Bengali"),"ne":("नेपाली","Nepali"),"dv":("ދިވެހި","Dhivehi"),"ps":("پښتو","Pashto"),"fa":("فارسی","Persian"),"my":("မြန်မာ","Burmese"),"zh":("中文","Chinese"),"ja":("日本語","Japanese"),"ko":("한국어","Korean"),"ms":("Melayu","Malay"),"th":("ไทย","Thai"),"id":("Indonesia","Indonesian"),"tl":("Filipino","Filipino"),"vi":("Tiếng Việt","Vietnamese"),"fr":("Français","French"),"de":("Deutsch","German"),"it":("Italiano","Italian"),"es":("Español","Spanish"),"nl":("Nederlands","Dutch"),"sv":("Svenska","Swedish"),"no":("Norsk","Norwegian"),"ru":("Русский","Russian"),"uk":("Українська","Ukrainian"),"pl":("Polski","Polish"),"tr":("Türkçe","Turkish"),"he":("עברית","Hebrew"),"ar":("العربية","Arabic"),"zu":("isiZulu","Zulu"),"af":("Afrikaans","Afrikaans"),"ha":("Hausa","Hausa"),"yo":("Yorùbá","Yoruba"),"sw":("Kiswahili","Swahili"),"am":("አማርኛ","Amharic"),"pt":("Português","Portuguese"),"km":("ខ្មែរ","Khmer"),"lo":("ລາວ","Lao"),"mn":("Монгол","Mongolian"),"kk":("Қазақша","Kazakh"),"uz":("Oʻzbek","Uzbek"),"az":("Azərbaycan","Azerbaijani"),"ka":("ქართული","Georgian"),"hy":("Հայերեն","Armenian"),"rw":("Kinyarwanda","Kinyarwanda"),"so":("Soomaali","Somali"),"el":("Ελληνικά","Greek"),"cs":("Čeština","Czech"),"hu":("Magyar","Hungarian"),"ro":("Română","Romanian"),"sr":("Српски","Serbian"),"hr":("Hrvatski","Croatian"),"da":("Dansk","Danish"),"fi":("Suomi","Finnish"),"is":("Íslenska","Icelandic"),"be":("Беларуская","Belarusian"),"lt":("Lietuvių","Lithuanian"),"ht":("Kreyòl ayisyen","Haitian Creole"),"mi":("Māori","Māori"),"cy":("Cymraeg","Welsh"),"ga":("Gaeilge","Irish"),"tw":("Twi","Twi")}

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

def fetch(url, limit=None, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit) if limit else r.read()

def up(u):
    """Browsers block http:// pictures on an https:// site, so upgrade them."""
    if u.startswith("//"): return "https:" + u
    if u.startswith("http://"): return "https://" + u[7:]
    return u

def parse(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for it in list(root.iter("item")) + list(root.iter(ATOM + "entry")):
        d = {"title": "", "link": "", "desc": "", "date": None, "image": "", "source": "", "vid": ""}
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
            elif n == "videoId": d["vid"] = txt
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
        d["image"] = up(d["image"]) if d["image"] else ""
        out.append(d)
    return out

def page_meta(url):
    """Reads the main picture and description from the article page itself."""
    if counters["og"] >= MAX_OG_FETCH or "news.google.com" in url or "bing.com" in url:
        return "", ""
    counters["og"] += 1
    img = desc = ""
    try:
        page = fetch(url, 300000, 8).decode("utf-8", "ignore")
        for pat in (r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                    r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']'):
            m = re.search(pat, page, re.I)
            if m:
                u = up(urllib.parse.urljoin(url, html.unescape(m.group(1))))
                if not re.search(r"logo|placeholder|default[-_]|favicon|sprite", u, re.I):
                    img = u; break
        for pat in (r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']',
                    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)'):
            m = re.search(pat, page, re.I)
            if m:
                desc = clean(html.unescape(m.group(1))); break
    except Exception:
        pass
    return img, desc

def og_image(url):
    return page_meta(url)[0]

def decode_gnews(url):
    """Turns a news.google.com article link into the publisher's own article link."""
    try:
        path = urllib.parse.urlparse(url).path.split("/")
        if "articles" not in path:
            return url
        b64 = path[-1]
        try:   # older links carry the address inside
            raw = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
            m = re.search(rb"https?://[^\x00-\x20\x7f-\xff\"<>]+", raw)
            if m and "." in m.group(0).decode("ascii", "ignore"):
                return m.group(0).decode("ascii", "ignore")
        except Exception:
            pass
        page = fetch(f"https://news.google.com/rss/articles/{b64}", 200000, 12).decode("utf-8", "ignore")
        sg = re.search(r'data-n-a-sg="([^"]+)"', page)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page)
        if not (sg and ts):
            return url
        inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
                 f'"{b64}",{ts.group(1)},"{sg.group(1)}"]')
        body = ("f.req=" + urllib.parse.quote(json.dumps([[["Fbv4je", inner]]]))).encode()
        req = urllib.request.Request("https://news.google.com/_/DotsSplashUi/data/batchexecute", body,
                                     {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8", "User-Agent": UA["User-Agent"]})
        with urllib.request.urlopen(req, timeout=15) as r:
            txt = r.read().decode("utf-8", "ignore")
        data = json.loads(txt.split("\n\n")[1][:-2])
        out = json.loads(data[0][2])[1]
        return out if out.startswith("http") else url
    except Exception:
        return url

def resolve_links(items):
    todo = [d for d in items if "news.google.com" in d["link"]]
    todo = todo[: max(0, MAX_GN - counters["gn"])]
    if not todo:
        return
    counters["gn"] += len(todo)
    with ThreadPoolExecutor(6) as ex:
        for d, u in zip(todo, ex.map(lambda x: decode_gnews(x["link"]), todo)):
            if u and "news.google.com" not in u:
                d["link"] = u

def looks_english(t):
    letters = [c for c in t if c.isalpha()]
    return not letters or sum(c < "\u0250" for c in letters) / len(letters) >= 0.8

def weak(d):
    t, x = d["title"].lower(), d["desc"].lower()
    return len(x) < 60 or x.startswith(t[:40])

def has_photo(d, cache):
    old = cache.get(d["link"] + "|en")
    return bool(d["image"]) or bool(old and old.get("imageType") == "photo")

def fill_images(items, cache):
    todo = [d for d in items if not has_photo(d, cache)]
    if not todo:
        return
    with ThreadPoolExecutor(6) as ex:
        for d, (u, desc) in zip(todo, ex.map(lambda x: page_meta(x["link"]), todo)):
            d["og_tried"] = True
            if u: d["image"] = u
            if desc and weak(d): d["desc"] = desc

def gemini_json(prompt, timeout=60):
    """Calls free Gemini. Tries several model names, because Google renames them."""
    if not GEMINI_KEY:
        return None
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"responseMimeType": "application/json"}}).encode()
    for m in ([_good["m"]] if _good["m"] else MODELS):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={GEMINI_KEY}"
        try:
            req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(json.load(r)["candidates"][0]["content"]["parts"][0]["text"])
            _good["m"] = m
            return out
        except urllib.error.HTTPError as e:
            detail = e.read()[:160].decode("utf-8", "ignore").replace("\n", " ")
            print(f"  Gemini {m}: HTTP {e.code} {detail}")
            _good["m"] = None
            if e.code in (400, 404):
                continue
            if e.code == 429:
                time.sleep(20)
            return None
        except Exception as e:
            print(f"  Gemini {m}: {e}")
            _good["m"] = None
            return None
    return None

def ai(title, text):
    if not GEMINI_KEY or counters["ai"] >= MAX_AI_TEXT:
        return None
    counters["ai"] += 1
    j = gemini_json("You write for a news site. Using ONLY the facts below, reply with JSON: "
                    '{"summary":"2 neutral sentences"}\n' f"Headline: {title}\nText: {text}", 30)
    try:
        return j["summary"], ""
    except Exception:
        return None

def flag(code):
    return "".join(chr(127397 + ord(c)) for c in code)

def colors(code):
    h = sum(ord(c) * 37 for c in code) % 360
    return [f"hsl({h},55%,26%)", f"hsl({(h + 35) % 360},60%,45%)"]

def get_items(country, extra):
    items, got = [], {}
    sources = [(n, u) for n, u in extra.get(country, {}).get("en", [])]
    q = urllib.parse.quote(country)
    sources.append(("Bing", f"https://www.bing.com/news/search?q={q}&format=rss&setlang=en"))
    sources.append(("Google", f"https://news.google.com/rss/search?q={q}+when:2d&hl=en&gl=US&ceid=US:en"))
    for name, url in sources:
        try:
            part = parse(fetch(url))
            got[name] = len(part); items += part
        except Exception as e:
            got[name] = "FAILED"
            print(f"  feed failed ({country} / {name}): {e}")
        time.sleep(0.4)
    print("  sources:", got)
    return [d for d in items if looks_english(d["title"])]

def translate(stories, lang):
    """Translates headlines + summaries with free Gemini, many stories in one call."""
    if not GEMINI_KEY or counters["tr"] >= MAX_TR_CALLS or lang not in LANGS or not stories:
        return None
    counters["tr"] += 1
    payload = [{"i": i, "title": x["title"], "summary": x["summary"]} for i, x in enumerate(stories)]
    arr = gemini_json(f"Translate these news headlines and summaries into {LANGS[lang][1]}. Keep names, numbers and facts exact. "
                      "Reply with ONLY a JSON array of objects with keys i, title, summary.\n" + json.dumps(payload, ensure_ascii=False))
    time.sleep(4)
    try:
        return {int(x["i"]): x for x in arr}
    except Exception:
        print(f"  translation to {lang} failed")
        return None

KEYS = {
 "sport": r"sport|cricket|football|soccer|tennis|olympic|league|tournament|golf|rugby|fifa|nba|ipl|wicket|athlet|marathon|formula 1|f1\b|champion",
 "business": r"business|econom|market|stock|bank|trade|inflation|tax|budget|invest|compan|oil price|rupee|dollar|profit|shares|gdp|export|import|tariff|currency",
 "health": r"health|hospital|disease|virus|vaccine|doctor|medical|cancer|covid|patient|medicine|outbreak|dengue|mental|surgery|drug",
 "technology": r"tech|\bai\b|artificial intelligence|software|\bapp\b|google|apple|microsoft|cyber|robot|smartphone|chip|internet|startup|data breach|digital|openai|satellite",
 "culture": r"film|movie|music|\bart\b|festival|culture|actor|singer|concert|book|theatre|heritage|fashion|celebrity|award|cinema|album",
 "travel": r"travel|tourism|tourist|flight|airline|airport|hotel|visa|holiday|destination|cruise|railway|passenger",
 "earth": r"climate|environment|earthquake|flood|storm|cyclone|wildlife|weather|rainfall|drought|volcano|tsunami|pollution|forest|ocean|species|wildfire|monsoon|science|space|nasa|landslide"}

def classify(text):
    text = text.lower()
    best, score = "general", 0
    for c, pat in KEYS.items():
        n = len(re.findall(pat, text))
        if n > score: best, score = c, n
    return best

def make_story(d, country, code, lang, now, cache, cat=None):
    old = cache.get(d["link"] + "|" + lang)
    breaking = (now - d["date"]).total_seconds() < BREAKING_HOURS * 3600
    if old:
        old = dict(old); old["breaking"] = breaking; old.setdefault("cat", "general")
        if old.get("imageType") != "photo":
            old["image"], old["imageType"] = (up(d["image"]), "photo") if d["image"] else ("", "art")
        return old
    res = ai(d["title"], d["desc"]) if lang == "en" else None
    summary = res[0] if res else (d["desc"][:220] + ("…" if len(d["desc"]) > 220 else ""))
    if not summary or summary.lower().startswith(d["title"].lower()[:40]):
        summary = ""
    real = up(d["image"]) if d["image"] else ("" if d.get("og_tried") else og_image(d["link"]))
    english = lang == "en" and looks_english(d["title"])
    return {"country": country, "code": code, "flag": flag(code), "c": colors(code), "emoji": "📰", "lang": lang,
            "title": d["title"], "summary": summary, "image": real, "imageType": "photo" if real else "art",
            "cat": cat or (classify(d["title"] + " " + d["desc"]) if english else "general"),
            "published": d["date"].isoformat(), "breaking": breaking,
            "url": d["link"], "refs": [[d["source"], d["link"]]]}

TZ_OFFSET = float(os.environ.get("TZ_OFFSET_HOURS", "5.5"))   # 5.5 = Sri Lanka time, used to decide which day a story belongs to
KEEP_DAYS = 30

def day_of(iso):
    return (datetime.fromisoformat(iso) + timedelta(hours=TZ_OFFSET)).strftime("%Y-%m-%d")

def save_archive(stories):
    """Keeps one file per day (archive/YYYY-MM-DD.json) so people can read old days."""
    os.makedirs("archive", exist_ok=True)
    by = {}
    for x in stories:
        by.setdefault(day_of(x["published"]), []).append(x)
    for day, items in by.items():
        path = f"archive/{day}.json"
        saved = {}
        if os.path.exists(path):
            try:
                saved = {y["url"] + "|" + y.get("lang", "en"): y for y in json.load(open(path, encoding="utf-8"))}
            except Exception:
                pass
        for x in items:
            saved[x["url"] + "|" + x.get("lang", "en")] = x
        with open(path, "w", encoding="utf-8") as f:
            json.dump(list(saved.values()), f, ensure_ascii=False, separators=(",", ":"))
    days = sorted(n[:-5] for n in os.listdir("archive") if re.match(r"\d{4}-\d{2}-\d{2}\.json$", n))
    for d in days[:-KEEP_DAYS]:
        os.remove(f"archive/{d}.json")
    with open("archive/index.json", "w") as f:
        json.dump(days[-KEEP_DAYS:], f)

def main():
    cache, old_videos = {}, []
    if os.path.exists("news.json"):
        try:
            old_videos = json.load(open("news.json", encoding="utf-8")).get("videos", [])
            for x in json.load(open("news.json", encoding="utf-8"))["stories"]:
                cache[x["url"] + "|" + x.get("lang", "en")] = x
        except Exception:
            pass
    countries = json.load(open("countries.json", encoding="utf-8"))
    extra = json.load(open("feeds.json", encoding="utf-8"))
    now = datetime.now(timezone.utc)
    print("Gemini key:", "set" if GEMINI_KEY else "NOT SET (translations and AI summaries are off)")
    stories, languages = [], {}
    for country, info in countries.items():
        code, langs = info[0], (info[1:] or ["en"])
        languages[country] = [[l, LANGS[l][0]] for l in langs if l in LANGS]
        print("Country:", country)
        seen, cands = set(), []
        for d in sorted(get_items(country, extra), key=lambda x: x["date"], reverse=True):
            key = d["title"].lower()
            if d["title"] and d["link"] and key not in seen and (now - d["date"]).days < 3:
                seen.add(key); cands.append(d)
            if len(cands) == CAND: break
        resolve_links(cands)
        fill_images(cands, cache)
        picked = sorted(cands, key=lambda d: (not has_photo(d, cache), -d["date"].timestamp()))[:MAX_PER_COUNTRY]
        base = [make_story(d, country, code, "en", now, cache) for d in picked]
        stories += base
        todo_langs = [(l, base) for l in langs if l != "en"] + [(l, base[:1]) for l in HOME_LANGS if l not in langs]
        for lang, src in todo_langs:
            native = []
            for name, url in extra.get(country, {}).get(lang, []):
                try:
                    got = parse(fetch(url))
                    if lang in NONLATIN:
                        got = [g for g in got if not looks_english(g["title"])]
                    native += got
                except Exception as e:
                    print(f"  feed failed ({country} / {name}): {e}")
                time.sleep(0.4)
            if native:
                seen2, got = set(), []
                for d in sorted(native, key=lambda x: x["date"], reverse=True):
                    if d["title"] and d["link"] and d["title"] not in seen2 and (now - d["date"]).days < 3:
                        seen2.add(d["title"]); got.append(d)
                    if len(got) == MAX_PER_COUNTRY: break
                stories += [make_story(d, country, code, lang, now, cache) for d in got]
                continue
            todo = []
            for x in src:
                old = cache.get(x["url"] + "|" + lang)
                if old: stories.append(old)
                else: todo.append(x)
            res = translate(todo, lang)
            if res:
                for i, x in enumerate(todo):
                    if i in res and res[i].get("title"):
                        t = dict(x); t.update(lang=lang, title=res[i]["title"], summary=res[i].get("summary") or "", translated=True)
                        stories.append(t)
    have = {x["url"] for x in stories}
    for cat, conf in json.load(open("categories.json", encoding="utf-8")).items():
        print("Category:", cat)
        items = []
        srcs = [(n, u) for n, u in conf.get("feeds", [])] + [("", f"https://www.bing.com/news/search?q={urllib.parse.quote(q)}&format=rss&setlang=en") for q in conf.get("queries", [])]
        for name, url in srcs:
            try:
                got = parse(fetch(url))
                for g in got:
                    if name: g["source"] = name
                items += got
            except Exception as e:
                print(f"  category feed failed ({cat}): {e}")
            time.sleep(0.4)
        n, seen = 0, set()
        for d in sorted(items, key=lambda x: x["date"], reverse=True):
            if d["title"] and d["link"] and d["link"] not in have and d["title"].lower() not in seen and (now - d["date"]).days < 3:
                seen.add(d["title"].lower()); have.add(d["link"]); n += 1
                stories.append(make_story(d, "World", "", "en", now, cache, cat))
            if n == 12: break
    videos = []
    try:
        for country, chans in json.load(open("videos.json", encoding="utf-8")).items():
            for name, cid in chans:
                try:
                    for d in parse(fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"))[:4]:
                        if d["vid"]:
                            videos.append({"id": d["vid"], "title": d["title"], "channel": name, "country": country, "published": d["date"].isoformat()})
                except Exception as e:
                    print(f"  video feed failed ({name}): {e}")
                time.sleep(0.4)
    except Exception as e:
        print("videos.json problem:", e)
    videos = sorted(videos, key=lambda v: v["published"], reverse=True) or old_videos
    if not stories:
        print("No stories found; keeping the old news.json")
        return
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump({"updated": now.isoformat(), "languages": languages, "codes": {c: i[0] for c, i in countries.items()}, "videos": videos, "stories": stories}, f, ensure_ascii=False, indent=1)
    save_archive(stories)
    by = {}
    for x in stories: by[x.get("lang", "en")] = by.get(x.get("lang", "en"), 0) + 1
    print("stories per language:", by, "| photos:", sum(1 for x in stories if x["imageType"] == "photo"), "of", len(stories), "| translation calls:", counters["tr"])
    print("Saved", len(stories), "stories")

if __name__ == "__main__":
    main()
