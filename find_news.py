"""
Lab media robot. 100% free: no API keys, no accounts, no credit cards.

Every day it looks for public written content about one researcher (Google News,
Bing News, GDELT, plus any feeds of her own you add), decides whether each item is
really about HER, and saves the results in news.json (which the lab website reads).

How it knows who she is ("identity profile", rebuilt on every run):
  1. Her own publications on OpenAlex: co-authors, institutions, topics, paper titles.
  2. Seed words in config.json (affiliations, research topics) and negative words
     (things that share her surname, like Plaza Baquedano).
  3. What it learns from YOU: words common in items you approve get more weight,
     words common in items you block get less.
  4. A free open-source AI model (runs inside GitHub, no account): compares the meaning
     of each item with her papers and your approved items vs. things that are not her.

Only the Python standard library is used.
"""

import html
import json
import math
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
NEWS_PATH = os.path.join(HERE, "news.json")
IDENTITY_PATH = os.path.join(HERE, "identity.json")
REVIEW_PATH = os.path.join(HERE, "REVIEW.md")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 lab-media-bot"
TODAY = datetime.now(timezone.utc).date().isoformat()

STOPWORDS = set("""
a al algo ante antes como con contra cual cuando de del desde donde el ella ellos en entre era es esa ese eso esta este esto
fue ha han hay la las le les lo los mas me mi muy no nos o otra otro para pero por que quien se sea ser si sin sobre su sus
tambien te tiene todo tu un una uno unos y ya the of and to in for on with at by from is are was were be been this that it
its as an or not but about into over after more new how why what who which their they them our your can will has have had
""".split())

PLATFORM_DOMAINS = {
    "youtube": ["youtube.com", "youtu.be"], "vimeo": ["vimeo.com"], "tiktok": ["tiktok.com"],
    "instagram": ["instagram.com"], "facebook": ["facebook.com", "fb.watch"], "x": ["x.com", "twitter.com"],
    "linkedin": ["linkedin.com"], "reddit": ["reddit.com"], "bluesky": ["bsky.app"], "threads": ["threads.net"],
    "spotify": ["open.spotify.com", "spotify.com"], "apple podcasts": ["podcasts.apple.com"], "ivoox": ["ivoox.com"],
}
VIDEO_PLATFORMS = {"youtube", "vimeo", "tiktok"}
AUDIO_PLATFORMS = {"spotify", "apple podcasts", "ivoox"}
SOCIAL_PLATFORMS = {"instagram", "facebook", "x", "linkedin", "reddit", "bluesky", "threads"}

status = {}  # source name -> text, saved in news.json


# ============================================================ helpers

def log(*a):
    print(*a, flush=True)


def http(url, data=None, headers=None, timeout=25, method=None):
    h = {"User-Agent": UA, "Accept-Language": "es-CL,es;q=0.9,en;q=0.8"}
    h.update(headers or {})
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def get_json(url, **kw):
    return json.loads(http(url, **kw) or "{}")


def strip_html(s):
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def norm(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def has(text_norm, needle):
    """Accent/case-insensitive match. Short needles (UAI, EEG) must be whole words."""
    n = norm(needle).strip()
    if not n:
        return False
    if len(n) <= 4:
        return re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", text_norm) is not None
    return n in text_norm


def title_key(t):
    return re.sub(r"[^a-z0-9]", "", norm(t))[:120]


def url_key(u):
    u = (u or "").lower().split("#")[0]
    if "youtube.com/watch" in u:
        m = re.search(r"[?&]v=([\w-]{11})", u)
        return "yt:" + m.group(1) if m else u
    u = u.split("?")[0]
    return re.sub(r"^https?://(www\.|m\.)?", "", u).rstrip("/")


def domain(u):
    try:
        d = urllib.parse.urlparse(u).netloc.lower()
        return re.sub(r"^(www\.|m\.)", "", d)
    except Exception:
        return ""


def platform_of(url):
    d = domain(url)
    for name, doms in PLATFORM_DOMAINS.items():
        if any(d == x or d.endswith("." + x) for x in doms):
            return name
    return "web"


def category_of(platform):
    if platform in VIDEO_PLATFORMS:
        return "video"
    if platform in AUDIO_PLATFORMS:
        return "audio"
    if platform in SOCIAL_PLATFORMS:
        return "social"
    return "article"


def to_date(s):
    if not s:
        return ""
    s = str(s).strip()
    if re.fullmatch(r"\d{9,11}(\.\d+)?", s):
        return datetime.fromtimestamp(float(s), timezone.utc).date().isoformat()
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except Exception:
        pass
    m = re.match(r"(\d{4})(\d{2})(\d{2})T", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else ""


def guess_lang(text):
    t = " " + norm(text) + " "
    es = sum(t.count(w) for w in (" el ", " la ", " de ", " que ", " los ", " las ", " del ", " para ", " estudio ", " una "))
    en = sum(t.count(w) for w in (" the ", " of ", " and ", " to ", " for ", " study ", " how ", " brain ", " with "))
    if es == en:
        return ""
    return "Spanish" if es > en else "English"


def guess_kind(item):
    t = norm(item["title"] + " " + item.get("outlet", "") + " " + item["url"])
    cat = item["category"]
    if cat == "video":
        return "Talk" if any(w in t for w in ("charla", "talk", "conferencia", "seminario", "webinar", "clase")) else "Video"
    if cat == "audio":
        return "Podcast"
    if cat == "social":
        if re.search(r"linkedin\.com/in/|instagram\.com/[^/]+/?$|x\.com/[^/]+/?$|twitter\.com/[^/]+/?$", item["url"]):
            return "Profile"
        return "Post"
    if any(w in t for w in ("entrevista", "interview", "conversamos", "conversacion con", "q&a")):
        return "Interview"
    if any(w in t for w in ("newsroom", "eurekalert", "press release", "comunicado", "sala de prensa")):
        return "Press release"
    if item["source"].startswith("Web"):
        return "Web"
    return "News"


def tokens(text):
    return [w for w in re.findall(r"[a-z]{5,}", norm(text)) if w not in STOPWORDS]


def youtube_id(url):
    m = re.search(r"(?:v=|youtu\.be/|embed/|shorts/)([\w-]{11})", url or "")
    return m.group(1) if m else ""


def new_item(title, url, source, outlet="", date="", snippet="", exact=False, own=False, extra=None):
    plat = platform_of(url)
    it = {
        "title": strip_html(title)[:300], "url": url, "source": source,
        "outlet": outlet or domain(url), "date": date, "snippet": strip_html(snippet)[:600],
        "exact": exact, "own": own, "platform": plat, "category": category_of(plat),
    }
    if extra:
        it.update(extra)
    return it


def run_source(name, fn, needs=None):
    if needs and not all(os.environ.get(k) for k in needs):
        status[name] = "skipped (no key)"
        log(f"  {name}: skipped, missing {', '.join(needs)}")
        return []
    try:
        res = fn()
        status[name] = f"ok ({len(res)})"
        log(f"  {name}: {len(res)} results")
        return res
    except Exception as e:
        status[name] = f"failed ({str(e)[:80]})"
        log(f"  {name}: FAILED ({e})")
        return []


def is_exact_query(q, cfg):
    return q.count('"') >= 2 and any(norm(v) in norm(q) for v in cfg["name_variants"][:3])


# ============================================================ sources

def parse_rss(xml_text):
    root = ET.fromstring(xml_text)
    out = []
    for it in root.iter("item"):
        def txt(tag):
            el = it.find(tag)
            return el.text if el is not None and el.text else ""
        src = it.find("source")
        src_name = src.text if src is not None and src.text else ""
        src_url = src.get("url", "") if src is not None else ""
        for el in it:
            if el.tag.lower().endswith("}source") and el.text:
                src_name = src_name or el.text
        out.append({"title": txt("title"), "link": txt("link"), "pub": txt("pubDate"),
                    "desc": txt("description"), "src_name": src_name, "src_url": src_url})
    return out


def google_items(xml_text, label, exact):
    res = []
    for it in parse_rss(xml_text):
        title, outlet = it["title"].strip(), it["src_name"].strip()
        cut = title.rfind(" - ")
        if cut > 0 and (not outlet or title[cut + 3:].strip() == outlet):
            outlet = outlet or title[cut + 3:].strip()
            title = title[:cut].strip()
        item = new_item(title, it["link"], label, outlet, to_date(it["pub"]), "", exact)
        item["platform"], item["category"] = "web", "article"  # Google News links hide the real domain
        item["outlet_domain"] = domain(it["src_url"])
        res.append(item)
    return res


def src_google_news(cfg):
    out = []
    for q in cfg["news_searches"]:
        for hl, gl, ceid in (("es-419", "CL", "CL:es-419"), ("en-US", "US", "US:en")):
            url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"
            try:
                out += google_items(http(url), f"Google News ({gl})", is_exact_query(q, cfg))
            except Exception as e:
                log(f"    Google News {gl} '{q}' failed: {e}")
    if cfg.get("google_news_topic"):
        url = f"https://news.google.com/rss/topics/{cfg['google_news_topic']}?hl=es-419&gl=CL&ceid=CL:es-419"
        try:
            out += google_items(http(url), "Google News topic", False)
        except Exception as e:
            log(f"    Google News topic failed: {e}")
    return out


def src_bing_news(cfg):
    out = []
    for q in cfg["news_searches"]:
        for it in parse_rss(http("https://www.bing.com/news/search?format=rss&q=" + urllib.parse.quote(q))):
            link = it["link"]
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
            link = qs.get("url", [link])[0]
            out.append(new_item(it["title"], link, "Bing News", it["src_name"], to_date(it["pub"]),
                                it["desc"], is_exact_query(q, cfg)))
    return out


def src_gdelt(cfg):
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?mode=artlist&format=json&maxrecords=250"
           "&sort=datedesc&timespan=3months&query=" + urllib.parse.quote(f'"{cfg["person"]}"'))
    data = get_json(url, timeout=45)
    return [new_item(a.get("title", ""), a.get("url", ""), "GDELT", a.get("domain", ""),
                     to_date(a.get("seendate", "")), "", True) for a in data.get("articles", [])]


def src_own_channels(cfg):
    out = []
    own = cfg.get("own_channels", {})
    for cid in own.get("youtube_channel_ids", []):
        xml_text = http(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
        root = ET.fromstring(xml_text)
        ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
        author = root.findtext("a:author/a:name", "", ns)
        for e in root.findall("a:entry", ns):
            vid = e.findtext("yt:videoId", "", ns)
            out.append(new_item(e.findtext("a:title", "", ns), f"https://www.youtube.com/watch?v={vid}",
                                "Own YouTube channel", author, to_date(e.findtext("a:published", "", ns)),
                                "", True, True, {"video_id": vid}))
    for feed in own.get("rss_feeds", []):
        for it in parse_rss(http(feed)):
            out.append(new_item(it["title"], it["link"], "Own feed", it["src_name"] or domain(feed),
                                to_date(it["pub"]), it["desc"], True, True))
    return out


def collect(cfg):
    log("Searching...")
    found = []
    found += run_source("Google News", lambda: src_google_news(cfg))
    found += run_source("Bing News", lambda: src_bing_news(cfg))
    found += run_source("GDELT", lambda: src_gdelt(cfg))
    found += run_source("Own channels", lambda: src_own_channels(cfg))
    return found


# ============================================================ identity profile

def build_identity(cfg, db):
    """Who she is, rebuilt every run from OpenAlex + seeds + your decisions."""
    ident = {"built_on": TODAY, "coauthors": [], "institutions": [], "topics": [], "paper_titles": [],
             "positive": cfg["identity_seed"]["positive"], "negative": cfg["identity_seed"]["negative"],
             "learned": {}}
    try:
        old = json.load(open(IDENTITY_PATH, encoding="utf-8"))
    except Exception:
        old = {}

    aid = cfg.get("openalex_author")
    if aid:
        try:
            url = (f"https://api.openalex.org/works?filter=author.id:{aid}&per-page=200"
                   "&select=title,authorships,topics,abstract_inverted_index")
            works = get_json(url).get("results", [])
            co, inst, top = Counter(), Counter(), Counter()
            for w in works:
                if w.get("title"):
                    ident["paper_titles"].append(w["title"])
                    ident.setdefault("paper_texts", []).append(
                        (w["title"] + ". " + rebuild_abstract(w.get("abstract_inverted_index")))[:1200])
                for a in w.get("authorships", []):
                    name = (a.get("author") or {}).get("display_name", "")
                    if (a.get("author") or {}).get("id", "").endswith(aid):
                        for i in a.get("institutions", []):
                            inst[i.get("display_name", "")] += 1
                    elif name:
                        co[name] += 1
                for t in w.get("topics", []) or []:
                    top[t.get("display_name", "")] += 1
            ident["coauthors"] = [n for n, _ in co.most_common(40)]
            ident["institutions"] = [n for n, _ in inst.most_common(10) if n]
            ident["topics"] = [n for n, _ in top.most_common(15) if n]
            status["OpenAlex profile"] = f"ok ({len(works)} papers)"
        except Exception as e:
            status["OpenAlex profile"] = f"failed, using last profile ({str(e)[:60]})"
            for k in ("coauthors", "institutions", "topics", "paper_titles", "paper_texts"):
                ident[k] = old.get(k, [])

    # Learn from your decisions: approved/published vs blocked
    good, bad = Counter(), Counter()
    for it in db["published"]:
        good.update(set(tokens(it["title"] + " " + it.get("snippet", ""))))
    for it in db.get("rejected", []):
        bad.update(set(tokens(it["title"] + " " + it.get("snippet", ""))))
    ng, nb = max(1, len(db["published"])), max(1, len(db.get("rejected", [])))
    # Never learn from her own name or the seed words: those are fixed facts, not opinions
    protected = set(tokens(" ".join(cfg["name_variants"] + ident["positive"] + ident["negative"])))
    learned = {}
    for w in set(good) | set(bad):
        if w in protected:
            continue
        weight = math.log(((good[w] + 0.5) / ng) / ((bad[w] + 0.5) / nb))
        if weight > 0 and good[w] < 2:
            continue                      # positive only if seen in 2+ approved items
        if weight < 0 and (bad[w] < 2 or len(db.get("rejected", [])) < 3):
            continue                      # negative only after 3+ blocks and 2+ sightings
        if abs(weight) >= 0.7:
            learned[w] = round(max(-3, min(3, weight)), 2)
    ident["learned"] = dict(sorted(learned.items(), key=lambda kv: -abs(kv[1]))[:200])
    json.dump(ident, open(IDENTITY_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return ident


def rebuild_abstract(inv):
    if not inv:
        return ""
    words = {}
    for w, pos in inv.items():
        for p in pos:
            words[p] = w
    return " ".join(words[i] for i in sorted(words))


class Semantic:
    """
    Free open-source AI that runs inside GitHub (no account, no key).
    A multilingual sentence model turns texts into meaning vectors and compares each
    new item with what we know is her (her papers + items you approved) and with what
    is NOT her (negative examples + items you blocked).
    If the model can't load, the robot simply works without it.
    """

    def __init__(self, cfg, ident, db):
        self.ok = False
        sc = cfg.get("semantic", {})
        if not sc.get("enabled", True):
            status["Semantic AI"] = "off"
            return
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np
            self.np = np
            self.model = SentenceTransformer(sc.get("model", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"))
            pos = ident.get("paper_texts", [])[:80] + [x["title"] for x in db["published"]][-150:]
            neg = sc.get("negative_examples", []) + [x["title"] for x in db.get("rejected", [])][-150:]
            self.pos = self.encode(pos) if pos else None
            self.neg = self.encode(neg) if neg else None
            self.ok = self.pos is not None
            status["Semantic AI"] = f"ok ({len(pos)} examples of her, {len(neg)} of not-her)"
        except Exception as e:
            status["Semantic AI"] = f"not available, using rules only ({str(e)[:60]})"

    def encode(self, texts):
        return self.model.encode([t[:1000] for t in texts], normalize_embeddings=True, show_progress_bar=False)

    def compare(self, text):
        v = self.encode([text])[0]
        def top(m):
            if m is None or not len(m):
                return 0.0
            sims = self.np.sort(m @ v)[::-1][:3]
            return float(sims.mean())
        return top(self.pos), top(self.neg)


SEM = None


def score(item, ident, cfg, page_text=""):
    """Returns (points, reasons). Higher = more sure it is about her."""
    text = norm(" ".join([item["title"], item.get("snippet", ""), item.get("outlet", ""), page_text]))
    pts, why = 0, []

    full = [v for v in cfg["name_variants"] if " " in v and not v.lower().startswith(("dr", "baquedano"))]
    if any(has(text, v) for v in full):
        pts += 4; why.append("full name +4")
    elif item["exact"]:
        pts += 3; why.append("found by exact-name search +3")
    elif has(text, "baquedano") and (has(text, "constanza") or has(text, "coni") or has(text, "dra")):
        pts += 2; why.append("surname + first name +2")
    elif has(text, "baquedano"):
        why.append("only the surname +0")
    else:
        return -99, ["her name is not there"]
    if item.get("own"):
        pts += 6; why.append("her own channel +6")

    p = [w for w in ident["positive"] if has(text, w)]
    if p:
        add = min(8, 2 * len(p)); pts += add; why.append(f"research words {p[:4]} +{add}")
    c = [n for n in ident["coauthors"] if len(n) > 6 and has(text, n)]
    if c:
        add = min(6, 3 * len(c)); pts += add; why.append(f"co-authors {c[:3]} +{add}")
    i = [n for n in ident["institutions"] if len(n) > 6 and has(text, n)]
    if i:
        pts += 2; why.append(f"her institution +2")
    t = [x for x in ident["paper_titles"] if len(x) > 20 and has(text, x[:60])]
    if t:
        pts += 5; why.append("mentions one of her papers +5")
    tp = [x for x in ident["topics"] if has(text, x)]
    if tp:
        pts += 1; why.append("OpenAlex topic +1")
    neg = [w for w in ident["negative"] if has(text, w)]
    if neg:
        add = 5 * len(neg); pts -= add; why.append(f"other Baquedano {neg[:3]} -{add}")
    lw = sum(ident["learned"].get(w, 0) for w in set(tokens(text)))
    lw = max(-4, min(4, lw))
    if abs(lw) >= 0.5:
        pts += round(lw); why.append(f"learned from your choices {lw:+.1f}")
    if SEM is not None and SEM.ok:
        her, nother = SEM.compare(" ".join([item["title"], item.get("snippet", ""), page_text[:1500]]))
        sc = cfg.get("semantic", {})
        if nother > her + 0.05:
            pts -= 3; why.append(f"AI: closer to not-her ({her:.2f} vs {nother:.2f}) -3")
        elif her >= sc.get("strong", 0.50):
            pts += 3; why.append(f"AI: very similar to her work ({her:.2f}) +3")
        elif her >= sc.get("medium", 0.38):
            pts += 2; why.append(f"AI: similar to her work ({her:.2f}) +2")
    return pts, why


# ============================================================ checks and AI judge

PAYWALL_HINTS = ("suscribete para seguir leyendo", "contenido exclusivo para suscriptores", "subscribe to continue",
                 "subscribe to read", "this article is for subscribers", "paywall", "inicia sesion para continuar")


def fetch_page(url):
    """Returns (ok, text, paywall_suspected). Google News links can't be opened server-side."""
    if "news.google.com" in url:
        return True, "", False
    try:
        raw = http(url, timeout=20)
    except urllib.error.HTTPError as e:
        return e.code in (401, 403, 429), "", False
    except Exception:
        return False, "", False
    meta = " ".join(re.findall(r'<meta[^>]+(?:description|og:title|og:description)[^>]+content="([^"]*)"', raw, re.I))
    text = strip_html(raw)[:20000]
    pw = any(h in norm(text[:20000]) for h in PAYWALL_HINTS)
    return True, meta + " " + text, pw


# ============================================================ decisions

def matches_any(text, needles):
    t = norm(text)
    return any(has(t, n) for n in needles if n)


def decide(item, ident, cfg):
    """Returns (verdict, reasons, extra) with verdict in skip / review / publish."""
    hay = f"{item['title']} {item['outlet']} {item['url']}"
    if matches_any(hay, cfg.get("blocklist", [])):
        return "skip", ["blocklisted"], {}
    if item["date"] and item["date"][:4].isdigit() and int(item["date"][:4]) < cfg.get("oldest_year", 2000):
        return "skip", ["too old"], {}
    if not item["title"] or not item["url"]:
        return "skip", ["empty"], {}

    pts, why = score(item, ident, cfg)
    if pts < cfg["thresholds"]["review"] - 2:
        return "skip", why, {"score": pts}

    ok, page, paywall = (True, "", False)
    if item["category"] == "article":
        ok, page, paywall = fetch_page(item["url"])
        pts, why = score(item, ident, cfg, page)
    extra = {"score": pts}
    if pts < cfg["thresholds"]["review"]:
        return "skip", why, extra
    if not ok:
        return "review", why + ["the link did not open"], extra
    if paywall:
        why.append("may be behind a paywall")

    cat = "own_channels" if item.get("own") else ("news" if item["source"].startswith(("Google", "Bing", "GDELT"))
                                                   else ("web" if item["category"] == "article" else item["category"]))
    allowed = cfg["auto_publish"].get(cat, False) and not paywall

    if allowed and pts >= cfg["thresholds"]["publish"]:
        return "publish", why, extra

    return "review", why, extra


# ============================================================ main

def to_entry(item, why, extra):
    e = {
        "category": item["category"], "platform": item["platform"], "kind": guess_kind(item),
        "outlet": item["outlet"] or item.get("outlet_domain", ""), "date": item["date"],
        "lang": guess_lang(item["title"] + " " + item.get("snippet", "")),
        "title": item["title"], "url": item["url"], "snippet": item.get("snippet", "")[:280],
        "found_by": item["source"], "found_on": TODAY, "score": extra.get("score"),
        "why": why,
    }
    vid = item.get("video_id") or (youtube_id(item["url"]) if item["platform"] == "youtube" else "")
    if vid:
        e["video_id"] = vid
    if item.get("thumb"):
        e["thumb"] = item["thumb"]
    return e


def main():
    cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
    db = json.load(open(NEWS_PATH, encoding="utf-8"))
    for k in ("published", "review", "rejected"):
        db.setdefault(k, [])

    # 1) Your decisions from config.json
    approve, block = cfg.get("approve", []), cfg.get("blocklist", [])
    keep = []
    for it in db["review"]:
        hay = f"{it['title']} {it['outlet']} {it['url']}"
        if matches_any(hay, block):
            db["rejected"].append({"title": it["title"], "url": it["url"], "snippet": it.get("snippet", "")})
        elif matches_any(hay, approve):
            it["approved_on"] = TODAY
            db["published"].append(it)
        else:
            keep.append(it)
    db["review"] = keep
    still = []
    for it in db["published"]:
        if matches_any(f"{it['title']} {it['outlet']} {it['url']}", block):
            db["rejected"].append({"title": it["title"], "url": it["url"], "snippet": it.get("snippet", "")})
        else:
            still.append(it)
    db["published"] = still

    # 2) Who she is
    ident = build_identity(cfg, db)
    global SEM
    SEM = Semantic(cfg, ident, db)
    log(f"Identity: {len(ident['coauthors'])} co-authors, {len(ident['paper_titles'])} papers, "
        f"{len(ident['learned'])} learned words")

    # 3) Search and decide
    found = collect(cfg)
    known_t = {title_key(x["title"]) for x in db["published"] + db["review"] + db["rejected"]}
    known_u = {url_key(x["url"]) for x in db["published"] + db["review"] + db["rejected"]}
    new_pub, new_rev, skipped = [], [], 0
    for item in found:
        tk, uk = title_key(item["title"]), url_key(item["url"])
        if not tk or tk in known_t or uk in known_u:
            continue
        known_t.add(tk); known_u.add(uk)
        verdict, why, extra = decide(item, ident, cfg)
        if verdict == "skip":
            skipped += 1
            continue
        entry = to_entry(item, why, extra)
        (new_pub if verdict == "publish" else new_rev).append(entry)

    db["published"] += new_pub
    db["review"] += new_rev
    for k in ("published", "review"):
        db[k].sort(key=lambda x: x.get("date") or "0000", reverse=True)
    db["last_checked"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    db["sources"] = status
    db["counts"] = dict(Counter(x["category"] for x in db["published"]))

    json.dump(db, open(NEWS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    write_review(db)

    log(f"\nPublished +{len(new_pub)} | review +{len(new_rev)} | ignored {skipped}")
    for e in new_pub:
        log(f"  + [{e['platform']}] {e['date']} {e['outlet']}: {e['title']}  (score {e['score']})")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"new_published={len(new_pub)}\nnew_review={len(new_rev)}\n")
    return 0


def write_review(db):
    L = ["# Waiting for your review", "",
         "The robot found these but was not sure, so they are **not** on the website yet.", "",
         "- **Show it:** copy the approve line into `approve` in `config.json`.",
         "- **Hide it forever:** paste the same line into `blocklist`. The robot also learns from this.",
         "- Saving `config.json` re-runs the robot within a couple of minutes.", "",
         f"_Last check: {db.get('last_checked','')}_", "", "## Sources", ""]
    for k, v in db.get("sources", {}).items():
        L.append(f"- {k}: {v}")
    L += ["", f"## Items ({len(db['review'])})", ""]
    if not db["review"]:
        L.append("Nothing to review right now.")
    for it in db["review"]:
        L += [f"### {it['title']}",
              f"**{it['platform']}** · {it['outlet']} · {it['date'] or 'no date'} · score {it.get('score')}", ""]
        if it.get("snippet"):
            L += [f"> {it['snippet'][:240]}", ""]
        L += [f"Why: {'; '.join(it.get('why', []))}", "", f"[Open]({it['url']})", "", "Approve line:", "```",
              json.dumps(it["title"][:80], ensure_ascii=False) + ",", "```", ""]
    open(REVIEW_PATH, "w", encoding="utf-8").write("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())
