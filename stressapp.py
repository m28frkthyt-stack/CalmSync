# app.py
import streamlit as st
import requests
import math
import time
import urllib.parse
from random import randint, random
from datetime import datetime, timedelta, timezone

APP_VERSION = "v0.9.0-demo"

st.set_page_config(page_title="Stress-Aware Break Scheduler", page_icon="🧠", layout="centered")

# ──────────────────────────────────────────────────────────────────────────────
# CSS (pastel overlay + centered/bordered graph box + footer)
# ──────────────────────────────────────────────────────────────────────────────
def read_css_file(name: str) -> str:
    try:
        with open(name, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

PASTEL_OVERLAY = """
:root { --pink-bg:#fff3f8; --pink-acc:#ff6aa9; --pink-acc-2:#f9a7c1; --ink:#5a3f4a; --ink-2:#6d2f4a; }
body { background: var(--pink-bg) !important; }
.wrapper { max-width:320px; margin:0 auto; }
.badge { background:#ffd7e6 !important; color:#8a3a5b !important; border-radius:999px; padding:6px 10px; font-weight:600; }
.card, .block, .rec-card { background:#ffffffcc !important; box-shadow:0 8px 30px rgba(255,106,169,0.15)!important; border-radius:16px!important; }
.rec-title, .h1, .hello { color:var(--ink-2) !important; }
.sub, .p, .small { color:#5a3f4a !important; }
.header .sub { margin-top:2px; font-size:0.92rem; }

.stButton>button[kind="primary"], .stButton>button {
  border-radius:999px !important; border:0 !important; padding:10px 14px !important;
  background: linear-gradient(90deg, var(--pink-acc), var(--pink-acc-2)) !important;
  color:white !important; font-weight:700 !important; margin: 6px 0 14px 0 !important;
}
.stButton>button:hover { filter:brightness(0.98); }

.kv { display:flex; justify-content:space-between; gap:12px; margin:6px 0; }
.kv span:first-child { color:#7f5b69; }
.kv span:last-child { font-weight:600; color:#492635; }

/* Stress visual: centered box with label */
.stress-visual-wrap { display:flex; justify-content:center; }
.stress-visual {
  max-width:288px; width:100%;
  border-radius:12px; overflow:hidden; background:#fff;
  border:1px solid #f7c2d6;
  display:flex; flex-direction:column; align-items:center; gap:6px; padding:8px;
}
.viz-label {
  font-size:0.78rem; color:#7f5b69; background:#fff0f6; border:1px solid #f7c2d6;
  padding:2px 8px; border-radius:999px; display:inline-block;
}

.fav-wrap { display:flex; flex-wrap:wrap; justify-content:center; gap:8px; margin-top:8px; }
.fav-pill { background:#ffeaf2; color:#6d2f4a; border:1px solid #f7c2d6; padding:6px 10px; border-radius:999px; font-size:0.9rem; font-weight:600; }

.spacer-8 { height:8px; }
.spacer-12 { height:12px; }
.spacer-16 { height:16px; }

.section-title { display:flex; align-items:center; gap:8px; }
.section-title .label { background:#ffd7e6; color:#8a3a5b; padding:4px 8px; border-radius:10px; font-weight:600; }

.explain { font-size:0.9rem; color:#6a4b58; font-style:italic; margin-top:4px; }

/* Footer */
.footer { text-align:center; color:#8a3a5b; font-size:0.8rem; opacity:0.9; margin: 18px 0 6px; }
"""

def inject_css():
    page = st.session_state.get("page", "initial")
    css = read_css_file("initial.css") if page == "initial" else (
          read_css_file("homepage.css") if page in ("home", "accept", "rec") else read_css_file("afterquestions.css"))
    st.markdown(f"<style>{css}\n{PASTEL_OVERLAY}</style>", unsafe_allow_html=True)

def render_footer():
    st.markdown(f"<div class='footer'>{APP_VERSION}</div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────────────
# Session state
# ──────────────────────────────────────────────────────────────────────────────
def ss_init():
    ss = st.session_state
    ss.setdefault("page", "initial")                # "initial" | "home" | "rec" | "accept" | "after"
    ss.setdefault("all_activities", [
        "Walk outside", "Stretch", "Breathe 4-7-8", "Tea break",
        "Power nap", "Quick tidy-up", "Listen to calm track"
    ])
    ss.setdefault("favorite_activities", [])
    ss.setdefault("custom_activities", [])
    # model: learned value (avg reward) and a small user preference term (kept for extensibility)
    ss.setdefault("model", {"overall": {}})
    ss.setdefault("last_recommendation", None)      # {activity, start?, duration?}
    ss.setdefault("epsilon", 0.05)                  # exploration
    ss.setdefault("tau", 0.8)                       # softmax temp
    ss.setdefault("calendar_url", "")
    ss.setdefault("calendar_events", [])
    ss.setdefault("calendar_last_status", "")
    ss.setdefault("accept_duration", 15)
    ss.setdefault("accept_start_choice", None)
    ss.setdefault("demo_day_offset", 0)
    ss.setdefault("fitbit_series_today", None)
    ss.setdefault("fitbit_peaks_today", 0)
    ss.setdefault("fitbit_series_today_key", None)
    for a in ss["all_activities"]:
        ss["model"]["overall"].setdefault(a, {"n":0,"value":0.0,"pref":0.0})

ss_init()
inject_css()

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def now_local() -> datetime:
    return datetime.now() + timedelta(days=st.session_state.get("demo_day_offset", 0))

# Weather (header info only; not used by model)
EHV_LAT, EHV_LON = 51.4416, 5.4697
def fetch_weather():
    try:
        url = ("https://api.open-meteo.com/v1/forecast?"
               f"latitude={EHV_LAT}&longitude={EHV_LON}&hourly=precipitation,wind_speed_10m&timezone=Europe%2FAmsterdam")
        r = requests.get(url, timeout=7); r.raise_for_status()
        data = r.json()
        times = data["hourly"]["time"]; precip = data["hourly"]["precipitation"]; wind = data["hourly"]["wind_speed_10m"]
        demo_iso = now_local().strftime("%Y-%m-%dT%H:00:00")
        idx = times.index(demo_iso) if demo_iso in times else (len(times)-1)
        pr = float(precip[idx]); ws = float(wind[idx])
        return {"precip": pr, "wind": ws, "time_iso": times[idx]}
    except Exception:
        return st.session_state.get("weather") or {"precip": 0.0, "wind": 3.0, "time_iso": now_local().strftime("%Y-%m-%dT%H:00:00")}

def _to_local(dt_aware_or_naive: datetime) -> datetime:
    if dt_aware_or_naive.tzinfo is None: return dt_aware_or_naive
    return datetime.fromtimestamp(dt_aware_or_naive.timestamp())

def _parse_ics_dt(val: str):
    s = val.strip()
    if len(s) == 8 and s.isdigit():
        day = datetime.strptime(s, "%Y%m%d"); start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1); return ("allday", start, end)
    if s.endswith("Z"):
        dt = datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc); return _to_local(dt)
    try: return datetime.strptime(s, "%Y%m%dT%H%M%S")
    except Exception: return None

def _unfold_ics_lines(text: str):
    out = []
    for line in text.splitlines():
        if line.startswith(" "): 
            if out: out[-1] += line[1:]
        else: out.append(line)
    return out

def parse_ics(text: str):
    evs = []; lines = _unfold_ics_lines(text); in_ev = False
    dtstart_raw = dtend_raw = None; transp = busystatus = None
    for ln in lines:
        if ln.startswith("BEGIN:VEVENT"):
            in_ev=True; dtstart_raw=dtend_raw=None; transp=busystatus=None; continue
        if ln.startswith("END:VEVENT"):
            if in_ev and dtstart_raw:
                start=end=None; st_parsed=_parse_ics_dt(dtstart_raw)
                if isinstance(st_parsed, tuple) and st_parsed[0]=="allday": start, end = st_parsed[1], st_parsed[2]
                else: start = st_parsed
                if dtend_raw:
                    en_parsed = _parse_ics_dt(dtend_raw)
                    end = en_parsed[1] if (isinstance(en_parsed, tuple) and en_parsed[0]=="allday") else en_parsed
                if start and not end: end = start + timedelta(hours=1)
                tr = (transp or "").strip().upper(); bs = (busystatus or "").strip().upper()
                busy = not (tr=="TRANSPARENT" or bs=="FREE")
                if start and end and end > start: evs.append({"start":start,"end":end,"busy":busy})
            in_ev=False; continue
        if not in_ev: continue
        if ln.startswith("DTSTART"): dtstart_raw = ln.split(":",1)[-1]
        elif ln.startswith("DTEND"): dtend_raw = ln.split(":",1)[-1]
        elif ln.startswith("TRANSP"): transp = ln.split(":",1)[-1]
        elif "BUSYSTATUS" in ln:     busystatus = ln.split(":",1)[-1]
    return evs

def fetch_and_cache_calendar(url: str):
    if not url.strip():
        st.session_state["calendar_last_status"] = "No calendar URL set."
        st.session_state["calendar_events"] = []; return
    try:
        r = requests.get(url, timeout=10); r.raise_for_status()
        events = parse_ics(r.text)
        now_real = datetime.now(); horizon = now_real + timedelta(days=30)
        busy = []
        for ev in events:
            if not ev.get("busy", True): continue
            s, e = ev["start"], ev["end"]
            if e < now_real or s > horizon: continue
            busy.append((s, e))
        busy.sort(key=lambda x: x[0])
        st.session_state["calendar_events"] = busy
        st.session_state["calendar_last_status"] = f"Loaded {len(busy)} busy events."
    except Exception as ex:
        st.session_state["calendar_events"] = []
        st.session_state["calendar_last_status"] = f"Failed to load calendar: {ex}"

def overlaps_busy(start_dt: datetime, end_dt: datetime) -> bool:
    for s, e in st.session_state.get("calendar_events", []):
        if start_dt < e and end_dt > s: return True
    return False

def list_available_slots(day_dt: datetime, duration_min: int):
    """Whole/half-hour slots, 08:00–22:00; hide past slots only for real today."""
    day = day_dt.date()
    earliest = datetime.combine(day, datetime.min.time()).replace(hour=8)
    latest   = datetime.combine(day, datetime.min.time()).replace(hour=22)
    step = timedelta(minutes=30); dur = timedelta(minutes=duration_min)
    real_today = datetime.now().date()
    cutoff = earliest if day > real_today else (datetime.now() if day == real_today else earliest)
    slots = []; cur = earliest
    while cur + dur <= latest:
        if cur >= cutoff and not overlaps_busy(cur, cur + dur):
            slots.append(cur)
        cur += step
    return slots

# ──────────────────────────────────────────────────────────────────────────────
# Bandit (ε-greedy + softmax) — learns from delta stress (−5..+5) + small exp boost
# ──────────────────────────────────────────────────────────────────────────────
def bandit_choose(favorites, epsilon=0.05, tau=0.8):
    model = st.session_state["model"]["overall"]
    scores = []
    for act in favorites:
        stats = model.get(act, {"n":0,"value":0.0,"pref":0.0})
        s = stats["value"] + stats["pref"]
        scores.append((act, s))
    if not scores:
        return None, {}
    if random() < epsilon:
        import random as _r
        return _r.choice([a for a, _ in scores]), dict(scores)
    m = max(s for _, s in scores)
    exps = [(a, math.exp((s - m) / max(1e-6, tau))) for a, s in scores]
    total = sum(v for _, v in exps) or 1.0
    r = random(); cum = 0.0
    for a, v in exps:
        p = v / total; cum += p
        if r <= cum: return a, dict(scores)
    return exps[-1][0], dict(scores)

def bandit_update(activity, delta_stress, exp_rating):
    """delta_stress: −5..+5 (positive means stress went down).
       reward = delta + small experience boost (can be negative)."""
    stats = st.session_state["model"]["overall"].setdefault(activity, {"n":0,"value":0.0,"pref":0.0})
    delta = float(delta_stress)         # −5..+5
    xp    = 0.2 * (float(exp_rating) - 5.0)  # −1.0..+1.0
    reward = delta + xp                  # may be negative → that's okay

    n = stats["n"] + 1
    stats["value"] = stats["value"] + (reward - stats["value"]) / n
    stats["n"] = n
    # keep pref available for future extensions; not used now

# ICS export
def make_ics(summary, start_dt, duration_min, description=""):
    end_dt = start_dt + timedelta(minutes=duration_min)
    fmt = lambda dt: dt.strftime("%Y%m%dT%H%M%S")
    uid = f"{int(time.time())}-{abs(hash(summary))}@stress-aware"
    desc = description.replace("\\n", " ")
    ics = [
        "BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//StressAware//BreakScheduler//EN","BEGIN:VEVENT",
        f"UID:{uid}", f"DTSTAMP:{fmt(now_local())}", f"DTSTART:{fmt(start_dt)}", f"DTEND:{fmt(end_dt)}",
        f"SUMMARY:{summary}", f"DESCRIPTION:{desc}", "END:VEVENT","END:VCALENDAR"
    ]
    return "\n".join(ics).encode("utf-8")

# ──────────────────────────────────────────────────────────────────────────────
# Demo stress series (boxed & centered) + inline SVG IMG
# ──────────────────────────────────────────────────────────────────────────────
def generate_demo_series(target_peaks: int, length: int = 48):
    baseline = randint(38, 55)
    series = [baseline + 6*math.sin(i/7.0) + 4*math.sin(i/3.5) for i in range(length)]
    centers, tries = [], 0
    while len(centers) < target_peaks and tries < 200:
        k = randint(2, length-3)
        if all(abs(k - c) >= 4 for c in centers):
            centers.append(k)
        tries += 1
    centers.sort()
    for c in centers:
        amp = randint(14, 28)
        for j in range(-3, 4):
            idx = max(0, min(length-1, c+j))
            weight = math.exp(-(j*j)/4.0)
            series[idx] += amp * weight
    series = [max(0, v + randint(-3, 4)) for v in series]
    return series

def ensure_series_for_demo_day():
    key = f"series_key_{st.session_state.get('demo_day_offset',0)}"
    if st.session_state.get("fitbit_series_today_key") == key:
        return
    peaks_today = randint(5, 7) if random() < 0.5 else randint(1, 4)
    series = generate_demo_series(peaks_today)
    st.session_state["fitbit_series_today"] = series
    st.session_state["fitbit_peaks_today"] = peaks_today
    st.session_state["fitbit_series_today_key"] = key

def series_to_svg(series, width=288, height=120, padding=8):
    if not series:
        return "<svg width='100%' height='120'></svg>"
    n = len(series); min_v = min(series); max_v = max(series); rng = max(1e-6, max_v - min_v)
    def sx(i): return padding + (width - 2*padding) * (i / max(1, n-1))
    def sy(v): return padding + (height - 2*padding) * (1 - (v - min_v) / rng)
    pts = " ".join([f"{sx(i):.1f},{sy(series[i]):.1f}" for i in range(n)])
    return f"""
    <svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="grad" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0%" stop-color="#ff6aa9" />
          <stop offset="100%" stop-color="#f9a7c1" />
        </linearGradient>
      </defs>
      <polyline points="{pts}" fill="none" stroke="url(#grad)" stroke-width="2" />
    </svg>
    """

def svg_img_tag(svg_str: str, w=288, h=120) -> str:
    encoded = urllib.parse.quote(svg_str)
    return f"<img alt='stress sparkline' src='data:image/svg+xml;utf8,{encoded}' width='{w}' height='{h}'/>"

# ──────────────────────────────────────────────────────────────────────────────
# Textual AI explanation (no numbers; adapted to delta scale)
# ──────────────────────────────────────────────────────────────────────────────
def expectation_text(activity: str) -> str:
    stats = st.session_state["model"]["overall"].get(activity, {"n": 0, "value": 0.0})
    n = stats.get("n", 0)
    mean = stats.get("value", 0.0)  # can be negative to positive

    if n == 0:
        return "I don’t know yet — let’s try this and learn what works for you."

    # Friendly mapping based on mean (rough bands for −5..+5)
    if mean <= -2.5:
        return "May increase stress for you; consider alternatives."
    if mean < -0.5:
        return "Tends to feel counter-productive based on past feedback."
    if mean < 0.5:
        return "Mixed effects so far — might help, might not."
    if mean < 2.5:
        return "Expected to provide a modest drop in stress."
    if mean < 4.0:
        return "Expected to provide a noticeable drop in stress."
    return "Often provides a strong reduction in stress."

# ──────────────────────────────────────────────────────────────────────────────
# Pages
# ──────────────────────────────────────────────────────────────────────────────
def page_initial():
    st.markdown("""
    <div class="wrapper">
      <div class="card">
        <div class="badge">Step 1</div>
        <div class="h1">Let’s create your personal advice!</div>
        <p class="p">Pick at least three activities you enjoy. You can add your own too.</p>
      </div>
    </div>
    """, unsafe_allow_html=True)

    for a in st.session_state["all_activities"]:
        col1, col2 = st.columns([0.15, 0.85], gap="small")
        with col1: st.checkbox("", key=f"chk_{a}")
        with col2: st.markdown(f"<div class='p'>{a}</div>", unsafe_allow_html=True)

    c1, c2 = st.columns([0.7, 0.3])
    with c1:
        new_act = st.text_input(" ", placeholder="Add your own activity", label_visibility="collapsed", key="custom_add")
    with c2:
        if st.button("Add"):
            if new_act.strip() and new_act not in st.session_state["all_activities"]:
                na = new_act.strip()
                st.session_state["all_activities"].append(na)
                st.session_state["custom_activities"].append(na)
                st.session_state["model"]["overall"].setdefault(na, {"n":0,"value":0.0,"pref":0.0})
                st.rerun()

    st.markdown("<div class='wrapper'><div class='section-title'><span class='label'>Calendar (.ics) URL</span></div></div>", unsafe_allow_html=True)
    cal_url = st.text_input("Calendar (.ics) URL", value=st.session_state.get("calendar_url",""))
    colc1, colc2 = st.columns(2)
    with colc1:
        if st.button("Save & Test", use_container_width=True):
            st.session_state["calendar_url"] = cal_url.strip()
            fetch_and_cache_calendar(st.session_state["calendar_url"])
    with colc2:
        if st.button("Clear", use_container_width=True):
            st.session_state["calendar_url"] = ""
            st.session_state["calendar_events"] = []
            st.session_state["calendar_last_status"] = "Cleared."
    if st.session_state.get("calendar_last_status"):
        st.caption(st.session_state["calendar_last_status"])

    st.markdown("<div class='wrapper'>", unsafe_allow_html=True)
    if st.button("Next →", use_container_width=True, type="primary"):
        selected = [a for a in st.session_state["all_activities"] if st.session_state.get(f"chk_{a}", False)]
        if len(selected) < 3:
            st.warning("Please pick at least three activities."); render_footer(); return
        st.session_state["favorite_activities"] = selected
        st.session_state["page"] = "home"
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)
    render_footer()

def page_home():
    # Header info (weather) + demo stress sparkline for deciding if a break is suggested today
    st.session_state["weather"] = fetch_weather()
    ensure_series_for_demo_day()
    series = st.session_state.get("fitbit_series_today") or []
    peaks = st.session_state.get("fitbit_peaks_today", 0)
    high_stress = peaks > 4

    day_label = now_local().strftime("%a %d %b")
    precip = f"{st.session_state['weather']['precip']:.1f}"
    wind = f"{st.session_state['weather']['wind']:.1f}"
    demo_badge = f"<span class='badge'>Demo day +{st.session_state.get('demo_day_offset',0)}</span>"
    cal_badge  = ("<span class='badge'>Calendar linked</span>" if st.session_state.get("calendar_events") else "<span class='badge'>No calendar</span>")

    st.markdown(f"""
    <div class="wrapper">
      <div class="header">
        <div class="hello">Good Morning, friend</div>
        <div class="sub">{day_label} · precip {precip} mm · wind {wind} m/s</div>
        <div style="margin-top:8px; display:flex; gap:8px;">{demo_badge} {cal_badge}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    svg = series_to_svg(series)
    st.markdown("""
    <div class="wrapper">
      <div class="block">
        <div class="badge">Stress Overview</div>
        <div class="stress-visual-wrap">
          <div class="stress-visual">
            <div class="viz-label">Demo stress (Fitbit-like)</div>
    """, unsafe_allow_html=True)
    st.markdown(svg_img_tag(svg), unsafe_allow_html=True)
    st.markdown(f"""
          </div>
        </div>
        <div class="sub" style="margin-top:8px; text-align:center;">Peaks today: <b>{peaks}</b></div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    status = ("High stress detected — a break is recommended"
              if high_stress else
              "No excess stress — you can schedule a break or skip to the next day")
    st.markdown(f"<div class='wrapper'><div class='badge'>{status}</div></div>", unsafe_allow_html=True)
    st.markdown("<div class='spacer-12'></div>", unsafe_allow_html=True)

    c1, c2, c3 = st.columns([0.34, 0.33, 0.33])
    if high_stress:
        go  = c1.button("Recommend break", use_container_width=True)
        nxt = None
    else:
        go  = c1.button("Suggest break (optional)", use_container_width=True)
        nxt = c2.button("Next day ▶", use_container_width=True)
    ref = c3.button("Refresh Calendar", use_container_width=True)

    if ref and st.session_state.get("calendar_url"):
        fetch_and_cache_calendar(st.session_state["calendar_url"]); st.rerun()
    if nxt:
        st.session_state["demo_day_offset"] += 1
        st.session_state["fitbit_series_today_key"] = None
        st.session_state["last_recommendation"] = None
        st.rerun()

    if go:
        favs = st.session_state["favorite_activities"]
        if favs:
            act, _ = bandit_choose(favs, st.session_state["epsilon"], st.session_state["tau"])
            st.session_state["last_recommendation"] = {"activity": act}
            st.session_state["page"] = "rec"; st.rerun()
        else:
            st.info("No favorites saved yet. Go back to add some.")

    st.markdown("<div class='spacer-16'></div>", unsafe_allow_html=True)

    st.markdown("<div class='wrapper'><div class='block'><div class='badge'>Your favorites</div>", unsafe_allow_html=True)
    if st.session_state["favorite_activities"]:
        st.markdown(
            "<div class='fav-wrap'>"
            + "".join([f"<span class='fav-pill'>{a}</span>" for a in st.session_state["favorite_activities"]])
            + "</div>", unsafe_allow_html=True
        )
    st.markdown("</div></div>", unsafe_allow_html=True)
    render_footer()

def page_rec():
    rec = st.session_state.get("last_recommendation")
    if not rec:
        st.session_state["page"] = "home"; st.rerun(); return
    act = rec["activity"]
    explain = expectation_text(act)

    st.markdown(f"""
    <div class="wrapper">
      <div class="rec-card">
        <div class="rec-title">Recommended break</div>
        <div class="kv"><span>Activity</span><span>{act}</span></div>
        <div class="explain">{explain}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    back = c1.button("Back", use_container_width=True)
    accept = c2.button("Accept", use_container_width=True)
    diff = c3.button("Suggest different", use_container_width=True)

    if back:
        st.session_state["page"] = "home"; st.rerun()
    if diff:
        favs = st.session_state.get("favorite_activities", [])
        if favs:
            alt_eps = min(0.5, st.session_state["epsilon"] + 0.2)
            new_act, _ = bandit_choose(favs, epsilon=alt_eps, tau=st.session_state["tau"])
            st.session_state["last_recommendation"] = {"activity": new_act}
        st.rerun()
    if accept:
        st.session_state["page"] = "accept"; st.rerun()
    render_footer()

def page_accept():
    rec = st.session_state.get("last_recommendation")
    if not rec:
        st.session_state["page"] = "home"; st.rerun(); return
    act = rec["activity"]

    st.session_state["accept_duration"] = st.slider("Duration (minutes)", 15, 60, int(st.session_state.get("accept_duration", 15)), step=5)

    slots = list_available_slots(now_local(), st.session_state["accept_duration"])
    if slots:
        if (st.session_state.get("accept_start_choice") not in slots):
            st.session_state["accept_start_choice"] = slots[0]
        labels = [s.strftime("%H:%M") for s in slots]
        try:
            idx = slots.index(st.session_state["accept_start_choice"])
        except ValueError:
            idx = 0
        chosen_label = st.selectbox("Start time", options=labels, index=idx)
        chosen_start = slots[labels.index(chosen_label)]
        st.session_state["accept_start_choice"] = chosen_start
    else:
        chosen_start = None
        st.info("No free whole/half-hour slots available today between 08:00–22:00 for that duration.")

    st.markdown(f"""
    <div class="wrapper">
      <div class="rec-card">
        <div class="rec-title">Start your break</div>
        <div class="kv"><span>Activity</span><span>{act}</span></div>
        <div class="kv"><span>Start</span><span>{chosen_start.strftime('%H:%M') if chosen_start else '—'}</span></div>
        <div class="kv"><span>Duration</span><span>{st.session_state["accept_duration"]} min</span></div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    did = c1.button("I did this break", use_container_width=True, disabled=(chosen_start is None))
    if chosen_start:
        ics = make_ics(
            summary=f"Break: {act}",
            start_dt=chosen_start,
            duration_min=st.session_state["accept_duration"],
            description=f"Suggested by Stress-Aware Scheduler."
        )
        c2.download_button("Plan (.ics)", data=ics, file_name=f"break_{act.replace(' ','_')}.ics",
                           mime="text/calendar", use_container_width=True)
    else:
        c2.write("")
    back = c3.button("Back", use_container_width=True)

    rec["start"] = chosen_start; rec["duration"] = st.session_state["accept_duration"]

    if back:
        st.session_state["page"] = "rec"; st.rerun()
    if did and chosen_start:
        st.session_state["page"] = "after"; st.rerun()
    render_footer()

def page_after():
    rec = st.session_state.get("last_recommendation")
    activity = rec["activity"] if rec else "(activity)"

    st.markdown(f"""
    <div class="wrapper">
      <div class="card">
        <div class="badge">Feedback</div>
        <div class="h1">How did it go?</div>
        <p class="p">Tell me how the break changed your stress.</p>
        <div class="p">Activity: <b>{activity}</b></div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # NEW: delta stress slider (centered scale −5..+5)
    delta = st.slider("How much did your stress change? (−5 = much worse, +5 = much better)",
                      min_value=-5, max_value=5, value=1, step=1)
    exp_rating  = st.slider("Experience rating (1–10)", 1, 10, 7)

    next_day_btn = st.button("Next day ▶", use_container_width=True)
    if next_day_btn:
        bandit_update(activity, delta_stress=delta, exp_rating=exp_rating)
        st.session_state["last_recommendation"] = None
        st.session_state["demo_day_offset"] += 1
        st.session_state["fitbit_series_today_key"] = None
        st.session_state["page"] = "home"
        st.rerun()
    render_footer()

# ──────────────────────────────────────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────────────────────────────────────
if st.session_state["page"] == "initial":
    page_initial()
elif st.session_state["page"] == "home":
    page_home()
elif st.session_state["page"] == "rec":
    page_rec()
elif st.session_state["page"] == "accept":
    page_accept()
else:
    page_after()