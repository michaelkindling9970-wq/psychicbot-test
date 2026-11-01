"""Psychic Predictions Bot - TEST MODE build. Entertainment only."""
import hashlib, hmac, json, os, random, re, time
from datetime import datetime
import requests
from flask import Flask, request, jsonify, redirect, make_response, render_template, abort

app = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
IMAGES = json.load(open(os.path.join(BASE, "images.json")))
TESTER_TOKEN = os.environ.get("TESTER_TOKEN", "")
COOKIE = "pb_tester"
BLOCKED_STATES = {"NY", "PA", "MA", "MD", "VA"}
RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_HOUR", "10"))
MAX_TOKENS = 220

# ---------------- AI providers (cheapest models as of Nov 2026) ----------------
PROVIDERS = [  # (env var, name, base_url, model)
    ("OPENAI_API_KEY", "openai", "https://api.openai.com/v1", "gpt-5-nano"),
    ("GEMINI_API_KEY", "gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash-lite"),
    ("GROQ_API_KEY", "groq", "https://api.groq.com/openai/v1", "openai/gpt-oss-20b"),
    ("XAI_API_KEY", "xai", "https://api.x.ai/v1", "grok-4.3"),
]

def active_provider():
    for env, name, url, model in PROVIDERS:
        if os.environ.get(env):
            return {"env": env, "name": name, "url": url, "model": model}
    return None

SYSTEM_PROMPT = """You write short, playful, ENTERTAINMENT-ONLY 'psychic' readings (3-4 sentences).
Tone: whimsical, warm, vague, clearly fictional fun. Theme it around the given mood/theme word.
NEVER mention or hint at: health, illness, pregnancy, death, accidents, money, finances, investing,
lottery, gambling, jobs, careers, legal matters, romance, relationships, marriage, breakups, family conflict,
any real or named person, politics, religion. NEVER give advice or instructions or claim real psychic power.
Only talk about small everyday delights: weather, colors, music, a nice walk, a good snack, a cozy evening,
curiosity, creativity, noticing something beautiful. Do not reference the user's location or device details
directly. End with nothing else - no disclaimers (the site adds them)."""

BLOCKLIST = re.compile(r"\b(" + "|".join([
    "health", "ill(ness)?", "sick", "disease", "cancer", "doctor", "hospital", "pregnan\\w*", "death", "die[sd]?", "dying",
    "dead", "accident", "money", "financ\\w*", "invest\\w*", "stock[s]?", "crypto\\w*", "lottery", "gambl\\w*", "bet(ting)?",
    "casino", "rich", "wealth", "debt", "job", "career", "boss", "promotion", "fired", "salary", "legal", "lawsuit", "court",
    "lawyer", "love", "lover", "romance", "romantic", "relationship", "marriage", "married", "divorce", "boyfriend",
    "girlfriend", "partner", "spouse", "breakup", "crush", "soulmate", "you should", "advice", "must", "guarantee\\w*",
]) + r")\b", re.I)

SAFE_STOCK = ("The stars whisper of a small, pleasant surprise: a song you forgot you loved, a sky with an unusual color, "
              "or a snack that tastes better than expected. Keep an eye out for something delightfully ordinary today.")

MOCK_OPENERS = ["The cards shimmer and reveal", "A {theme}-scented breeze suggests", "The crystal ball fogs, then clears to show",
                "Tonight's {theme} energy hints at"]
MOCK_MIDDLES = ["a tune that gets stuck in your head in the best way", "a perfectly timed cup of something warm",
                "a cloud shaped suspiciously like a teapot", "a color you'll notice everywhere", "a delightfully cozy {part}",
                "an unexpected reason to smile at a stranger's dog"]
MOCK_CLOSERS = ["Your {weekday} sparkles with tiny, harmless wonder.", "The universe winks - mostly about snacks.",
                "Spirits agree: it's a fine time to notice small beautiful things."]

def part_of_day(hour):
    return "morning" if 5 <= hour < 12 else "afternoon" if hour < 17 else "evening" if hour < 22 else "night"

def pick_theme(sig):
    hour = int(sig.get("hour", 12))
    pod = part_of_day(hour)
    month = int(sig.get("month", 6))
    if pod == "night":
        return random.choice(["stars", "moon"])
    if pod == "evening":
        return random.choice(["candle", "moon", "ocean"]) if sig.get("dark") else random.choice(["ocean", "candle"])
    if pod == "morning":
        return "bloom" if month in (3, 4, 5, 6) else "dawn"
    return random.choice(["ocean", "bloom", "dawn"])

def build_template(sig, geo):
    hour = int(sig.get("hour", 12))
    return {
        "part_of_day": part_of_day(hour),
        "weekday": sig.get("weekday", "today"),
        "season_month": sig.get("month"),
        "device": sig.get("device", "unknown"),
        "prefers_dark": bool(sig.get("dark")),
        "language": sig.get("lang", "en"),
        "region": (geo or {}).get("region_code") or "unknown",
        "theme": pick_theme(sig),
    }

def mock_reading(t):
    fmt = dict(theme=t["theme"], part=t["part_of_day"], weekday=t["weekday"])
    return " ".join([random.choice(MOCK_OPENERS).format(**fmt), random.choice(MOCK_MIDDLES).format(**fmt) + ".",
                     random.choice(MOCK_CLOSERS).format(**fmt)])

def ai_reading(t, prov):
    user = (f"Theme: {t['theme']}. Part of day: {t['part_of_day']}. Day: {t['weekday']}. "
            f"Mood: {'moody/dark' if t['prefers_dark'] else 'bright'}. Write the reading.")
    body = {"model": prov["model"], "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                                  {"role": "user", "content": user}]}
    body["max_completion_tokens" if prov["name"] == "openai" else "max_tokens"] = MAX_TOKENS
    r = requests.post(prov["url"] + "/chat/completions", json=body, timeout=20,
                      headers={"Authorization": "Bearer " + os.environ[prov["env"]]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def safe(text):
    if not text or BLOCKLIST.search(text):
        return SAFE_STOCK, True
    return text, False

def pick_image(theme):
    pool = [i for i in IMAGES if i["theme"] == theme] or IMAGES
    i = random.choice(pool)
    return {"src": "/static/img/" + i["file"], "by": i["by"],
            "profile": f"https://unsplash.com/@{i['user']}?utm_source=psychicbot_test&utm_medium=referral",
            "page": i["page"] + "?utm_source=psychicbot_test&utm_medium=referral"}

# ---------------- Geo ----------------
_geo_cache = {}

def client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    return xff.split(",")[0].strip() if xff else (request.remote_addr or "")

STATE_NAMES = {"new york": "NY", "pennsylvania": "PA", "massachusetts": "MA", "maryland": "MD", "virginia": "VA"}

def _norm_region(code, name):
    if code and len(code) == 2:
        return code.upper()
    return STATE_NAMES.get((name or "").strip().lower(), code or name)

def _get(url):
    return requests.get(url, timeout=4, headers={"User-Agent": "psychicbot-test/0.1"}).json()

def _p_ipapi_com(ip):  # free, non-commercial, HTTP only
    d = _get(f"http://ip-api.com/json/{ip}?fields=status,countryCode,region,regionName")
    if d.get("status") == "success":
        return d["countryCode"], _norm_region(d.get("region"), d.get("regionName"))

def _p_ipinfo(ip):
    d = _get(f"https://ipinfo.io/{ip}/json")
    if d.get("country"):
        return d["country"], _norm_region(None, d.get("region"))

def _p_freeipapi(ip):
    d = _get(f"https://freeipapi.com/api/json/{ip}")
    if d.get("countryCode"):
        return d["countryCode"], _norm_region(None, d.get("regionName"))

def _p_ipwhois(ip):
    d = _get(f"https://ipwho.is/{ip}")
    if d.get("success"):
        return d.get("country_code"), _norm_region(d.get("region_code"), d.get("region"))

def _p_ipapi_co(ip):
    d = _get(f"https://ipapi.co/{ip}/json/")
    if not d.get("error") and d.get("country_code"):
        return d["country_code"], _norm_region(d.get("region_code"), d.get("region"))

GEO_PROVIDERS = [("ip-api.com", _p_ipapi_com), ("ipinfo.io", _p_ipinfo), ("freeipapi.com", _p_freeipapi),
                 ("ipwho.is", _p_ipwhois), ("ipapi.co", _p_ipapi_co)]

def geo_lookup(ip, trace=None):
    """Returns dict(country_code, region_code, source) or None on failure (caller fails closed)."""
    if trace is None and ip in _geo_cache and time.time() - _geo_cache[ip][0] < 3600:
        return _geo_cache[ip][1]
    res = None
    for name, fn in GEO_PROVIDERS:
        try:
            out = fn(ip)
        except Exception as e:
            out = None
            if trace is not None: trace[name] = type(e).__name__
        else:
            if trace is not None: trace[name] = out or "no result"
        if out and out[0]:
            res = {"country_code": out[0], "region_code": out[1], "source": name}
            break
    if res:
        _geo_cache[ip] = (time.time(), res)
    return res

def geo_allowed(geo):
    if not geo:
        return False, "Location lookup failed (fail-closed)"
    if geo.get("country_code") != "US":
        return False, "Not available outside the United States"
    if (geo.get("region_code") or "").upper() in BLOCKED_STATES:
        return False, "Not available in your state"
    return True, "ok"

def _sig(token):
    return hmac.new(token.encode(), b"psychicbot-tester", hashlib.sha256).hexdigest()

def is_tester():
    return bool(TESTER_TOKEN) and hmac.compare_digest(request.cookies.get(COOKIE, ""), _sig(TESTER_TOKEN))

@app.before_request
def gate():
    if request.path.startswith("/static") or request.path in ("/healthz", "/robots.txt"):
        return
    if request.path == "/unlock":
        if request.method == "GET":
            return ('<!doctype html><meta name=viewport content="width=device-width"><title>Tester unlock</title>'
                    '<body style="font-family:sans-serif;background:#140b24;color:#eee;display:grid;place-items:center;height:100vh">'
                    '<form method=post><h2>Tester unlock</h2><input type=password name=tester placeholder="Passcode" autofocus '
                    'style="padding:10px;font-size:16px"> <button style="padding:10px 16px">Unlock</button></form>')
        tok = request.form.get("tester", "")
    else:
        tok = request.args.get("tester")
    if tok is not None:
        if TESTER_TOKEN and hmac.compare_digest(tok, TESTER_TOKEN):
            resp = make_response(redirect("/"))
            resp.set_cookie(COOKIE, _sig(TESTER_TOKEN), max_age=30 * 86400, httponly=True, secure=request.is_secure, samesite="Lax")
            return resp
        abort(403)
    if request.path == "/debug/geo":
        return  # handled (and protected) in the view
    if is_tester():
        return
    geo = geo_lookup(client_ip())
    ok, why = geo_allowed(geo)
    if not ok:
        return render_template("blocked.html", reason=why), 451

@app.after_request
def headers(resp):
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp

# ---------------- Rate limit ----------------
_hits = {}

def rate_ok(ip):
    now = time.time()
    h = [t for t in _hits.get(ip, []) if now - t < 3600]
    if len(h) >= RATE_LIMIT:
        _hits[ip] = h
        return False
    h.append(now)
    _hits[ip] = h
    return True

# ---------------- Routes ----------------
@app.route("/")
def index():
    prov = active_provider()
    return render_template("index.html", provider=prov, tester=is_tester())

@app.route("/robots.txt")
def robots():
    return "User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain"}

@app.route("/healthz")
def healthz():
    return "ok"

@app.route("/credits")
def credits():
    return render_template("credits.html", images=IMAGES)

@app.route("/api/reading", methods=["POST"])
def reading():
    data = request.get_json(silent=True) or {}
    if not (data.get("age_ok") and data.get("fun_ok")):
        return jsonify(error="Please confirm you are 18+ and understand this is for fun."), 400
    ip = client_ip()
    if not rate_ok(ip):
        return jsonify(error="Rate limit reached - the spirits need a nap. Try again later."), 429
    sig = data.get("signals") or {}
    geo = geo_lookup(ip) if not is_tester() else (geo_lookup(ip) or {})
    t = build_template(sig, geo)
    prov = active_provider()
    mode = "ai" if (prov and data.get("mode") != "mock") else "mock"
    text, err = None, None
    if mode == "ai":
        try:
            text = ai_reading(t, prov)
        except Exception as e:
            err = type(e).__name__
            mode, text = "mock (AI error fallback)", mock_reading(t)
    else:
        text = mock_reading(t)
    text, filtered = safe(text)
    used = {"browser": sig, "server": {"country": (geo or {}).get("country_code"), "region": (geo or {}).get("region_code"),
                                        "geo_source": (geo or {}).get("source")}, "template": t}
    return jsonify(reading=text, mode=mode, model=(prov or {}).get("model") if mode == "ai" else "template-mock",
                   filtered=filtered, error=err, image=pick_image(t["theme"]), used=used)

@app.route("/debug/geo")
def debug_geo():
    if not is_tester():
        abort(403)
    ip = request.args.get("ip", "")
    trace = {}
    geo = geo_lookup(ip, trace) if ip else None
    ok, why = geo_allowed(geo)
    if not ok:
        return render_template("blocked.html", reason=why, debug={"ip": ip, "geo": geo, "trace": trace}), 451
    return jsonify(ip=ip, geo=geo, allowed=ok, trace=trace)

if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 5055)))
