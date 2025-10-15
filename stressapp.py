# app.py
import streamlit as st
import requests
import math
import time
import urllib.parse
from random import randint, random
from datetime import datetime, timedelta, timezone

APP_VERSION = "v0.9.1-demo"

st.set_page_config(page_title="Stress-Aware Break Scheduler", page_icon="🧠", layout="centered")

# ──────────────────────────────────────────────────────────────────────────────
# CSS (pastel overlay + improved spacing + responsive text + centered graph)
# ──────────────────────────────────────────────────────────────────────────────
def read_css_file(name: str) -> str:
    try:
        with open(name, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

PASTEL_OVERLAY = """
:root {
  --pink-bg: #fff3f8;
  --pink-acc: #ff6aa9;
  --pink-acc-2: #f9a7c1;
  --ink: #5a3a50;
  --ink-2: #432838;
}

/* General layout */
body {
  background: var(--pink-bg) !important;
  font-family: 'Inter', system-ui, sans-serif;
  color: var(--ink);
}

.wrapper {
  max-width: 340px;
  margin: 0 auto;
  padding: 0 10px;
}

/* Cards and spacing rhythm */
.card, .block, .rec-card {
  background: #ffffffcc !important;
  box-shadow: 0 8px 30px rgba(255, 106, 169, 0.15) !important;
  border-radius: 18px !important;
  padding: 22px 18px !important;
  margin-top: 28px !important;
}

.section {
  margin-top: 26px !important;
}

.badge {
  background: #ffd7e6 !important;
  color: #8a3a5b !important;
  border-radius: 999px;
  padding: 6px 12px;
  font-weight: 600;
  display: inline-block;
}

.h1, .rec-title, .hello {
  color: var(--ink-2);
  font-weight: 700;
  line-height: 1.3;
  margin-bottom: 8px;
  font-size: 1.25rem;
}

.p, .sub {
  color: #5a3f4a;
  line-height: 1.55;
  word-break: break-word;
  font-size: 1rem;
}

/* Buttons */
.stButton>button[kind="primary"], .stButton>button {
  border-radius: 999px !important;
  border: 0 !important;
  padding: 12px 16px !important;
  background: linear-gradient(90deg, var(--pink-acc), var(--pink-acc-2)) !important;
  color: white !important;
  font-weight: 700 !important;
  margin: 10px 0 22px 0 !important;
  width: 100%;
  font-size: 1rem;
}

.stButton>button:hover {
  filter: brightness(0.98);
}

/* Stress graph area (centered and responsive) */
.stress-visual-wrap {
  display: flex;
  justify-content: center;
  align-items: center;
  margin-top: 18px;
  width: 100%;
}

.stress-visual {
  max-width: 320px;
  width: 100%;
  background: #fff;
  border-radius: 14px;
  border: 1px solid #f7c2d6;
  padding: 12px;
  box-sizing: border-box;
  text-align: center;
}

.viz-label {
  font-size: 0.8rem;
  color: #7f5b69;
  background: #fff0f6;
  border: 1px solid #f7c2d6;
  padding: 3px 10px;
  border-radius: 999px;
  margin-bottom: 8px;
  display: inline-block;
}

/* Favorite pills */
.fav-wrap {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
  margin-top: 12px;
}

.fav-pill {
  background: #ffeaf2;
  color: #6d2f4a;
  border: 1px solid #f7c2d6;
  padding: 7px 12px;
  border-radius: 999px;
  font-size: 0.9rem;
  font-weight: 600;
}

/* Key-value display (cleaner spacing) */
.kv {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  margin: 8px 0;
}

.kv span:first-child {
  color: #7f5b69;
}

.kv span:last-child {
  font-weight: 600;
  color: #492635;
}

/* Footer */
.footer {
  text-align: center;
  color: #8a3a5b;
  font-size: 0.85rem;
  opacity: 0.9;
  margin: 40px 0 14px;
}
"""

def inject_css():
    page = st.session_state.get("page", "initial")
    css = read_css_file("initial.css") if page == "initial" else (
          read_css_file("homepage.css") if page in ("home", "accept", "rec") else read_css_file("afterquestions.css"))
    st.markdown(f"<style>{css}\n{PASTEL_OVERLAY}</style>", unsafe_allow_html=True)

def render_footer():
    st.markdown(f"<div class='footer'>{APP_VERSION}</div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────────────
# Initialize session state
# ──────────────────────────────────────────────────────────────────────────────
def ss_init():
    ss = st.session_state
    ss.setdefault("page", "initial")
    ss.setdefault("all_activities", [
        "Walk outside", "Stretch", "Breathe 4-7-8", "Tea break",
        "Power nap", "Quick tidy-up", "Listen to calm track"
    ])
    ss.setdefault("favorite_activities", [])
    ss.setdefault("custom_activities", [])
    ss.setdefault("model", {"overall": {}})
    ss.setdefault("last_recommendation", None)
    ss.setdefault("epsilon", 0.05)
    ss.setdefault("tau", 0.8)
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
        ss["model"]["overall"].setdefault(a, {"n": 0, "value": 0.0, "pref": 0.0})

ss_init()
inject_css()

# ──────────────────────────────────────────────────────────────────────────────
# Utility functions (weather + ics parsing)
# ──────────────────────────────────────────────────────────────────────────────
def now_local() -> datetime:
    return datetime.now() + timedelta(days=st.session_state.get("demo_day_offset", 0))

EHV_LAT, EHV_LON = 51.4416, 5.4697
def fetch_weather():
    try:
        url = ("https://api.open-meteo.com/v1/forecast?"
               f"latitude={EHV_LAT}&longitude={EHV_LON}&hourly=precipitation,wind_speed_10m&timezone=Europe%2FAmsterdam")
        r = requests.get(url, timeout=7)
        data = r.json()
        times = data["hourly"]["time"]
        precip = data["hourly"]["precipitation"]
        wind = data["hourly"]["wind_speed_10m"]
        demo_iso = now_local().strftime("%Y-%m-%dT%H:00:00")
        idx = times.index(demo_iso) if demo_iso in times else (len(times)-1)
        return {"precip": float(precip[idx]), "wind": float(wind[idx])}
    except Exception:
        return {"precip": 0.0, "wind": 3.0}

# ──────────────────────────────────────────────────────────────────────────────
# Bandit model (ε-greedy + softmax) using delta stress feedback
# ──────────────────────────────────────────────────────────────────────────────
def bandit_choose(favorites, epsilon=0.05, tau=0.8):
    model = st.session_state["model"]["overall"]
    scores = [(a, model.get(a, {"value":0})["value"]) for a in favorites]
    if not scores: return None, {}
    if random() < epsilon:
        return scores[randint(0, len(scores)-1)][0], dict(scores)
    m = max(s for _, s in scores)
    exps = [(a, math.exp((s-m)/max(1e-6,tau))) for a,s in scores]
    total = sum(v for _,v in exps)
    r = random(); cum = 0
    for a,v in exps:
        cum += v/total
        if r <= cum: return a, dict(scores)
    return exps[-1][0], dict(scores)

def bandit_update(activity, delta_stress, exp_rating):
    stats = st.session_state["model"]["overall"].setdefault(activity, {"n":0,"value":0.0})
    reward = float(delta_stress) + 0.2*(float(exp_rating)-5.0)
    n = stats["n"]+1
    stats["value"] += (reward - stats["value"]) / n
    stats["n"] = n

# ──────────────────────────────────────────────────────────────────────────────
# Demo stress data
# ──────────────────────────────────────────────────────────────────────────────
def generate_demo_series(target_peaks, length=48):
    base = randint(38,55)
    series = [base + 5*math.sin(i/5.5)+randint(-3,3) for i in range(length)]
    for _ in range(target_peaks): 
        k = randint(3,length-4)
        for j in range(-2,3):
            idx = max(0,min(length-1,k+j))
            series[idx]+=randint(10,25)*(1-abs(j)/3)
    return series

def ensure_series_for_demo_day():
    key = f"series_{st.session_state.get('demo_day_offset',0)}"
    if st.session_state.get("fitbit_series_today_key")==key: return
    peaks_today = randint(5,7) if random()<0.5 else randint(1,4)
    st.session_state["fitbit_series_today"]=generate_demo_series(peaks_today)
    st.session_state["fitbit_peaks_today"]=peaks_today
    st.session_state["fitbit_series_today_key"]=key

def series_svg(series):
    n=len(series); w,h=288,120; pad=8
    mn,mx=min(series),max(series); rng=max(1,mx-mn)
    def sx(i): return pad+(w-2*pad)*(i/max(1,n-1))
    def sy(v): return pad+(h-2*pad)*(1-(v-mn)/rng)
    pts=" ".join([f"{sx(i):.1f},{sy(v):.1f}" for i,v in enumerate(series)])
    svg=f"<svg width='{w}' height='{h}' viewBox='0 0 {w} {h}' xmlns='http://www.w3.org/2000/svg'>"
    svg+="<polyline points='"+pts+"' fill='none' stroke='url(#grad)' stroke-width='2'/>"
    svg+="<defs><linearGradient id='grad' x1='0' x2='1'><stop offset='0%' stop-color='#ff6aa9'/>"
    svg+="<stop offset='100%' stop-color='#f9a7c1'/></linearGradient></defs></svg>"
    return svg

def svg_tag(svg): 
    enc=urllib.parse.quote(svg)
    return f"<img src='data:image/svg+xml;utf8,{enc}' width='288' height='120'/>"

# ──────────────────────────────────────────────────────────────────────────────
# Textual expectation based on mean value
# ──────────────────────────────────────────────────────────────────────────────
def expectation_text(activity):
    s=st.session_state["model"]["overall"].get(activity,{"n":0,"value":0})
    n=s["n"]; m=s["value"]
    if n==0: return "I don’t know yet — let’s see how it affects you."
    if m<=-2: return "May increase stress for you; consider alternatives."
    if m<-0.5: return "Tends to feel counterproductive."
    if m<0.5: return "Mixed results so far."
    if m<2.5: return "Expected to provide a gentle relief."
    if m<4: return "Expected to noticeably lower stress."
    return "Often provides a strong reduction in stress."

# ──────────────────────────────────────────────────────────────────────────────
# Pages
# ──────────────────────────────────────────────────────────────────────────────
def page_initial():
    st.markdown("""
    <div class='wrapper'>
      <div class='card'>
        <div class='badge'>Step 1</div>
        <div class='h1'>Let’s create your personal advice!</div>
        <p class='p'>Pick at least three activities you enjoy. You can add your own too.</p>
      </div>
    </div>
    """,unsafe_allow_html=True)
    for a in st.session_state["all_activities"]:
        col1,col2=st.columns([0.15,0.85])
        with col1: st.checkbox("",key=f"chk_{a}")
        with col2: st.markdown(f"<div class='p'>{a}</div>",unsafe_allow_html=True)
    if st.button("Next →",use_container_width=True,type="primary"):
        sel=[a for a in st.session_state["all_activities"] if st.session_state.get(f"chk_{a}",False)]
        if len(sel)<3: st.warning("Pick at least three."); return
        st.session_state["favorite_activities"]=sel
        st.session_state["page"]="home"; st.rerun()
    render_footer()

def page_home():
    st.session_state["weather"]=fetch_weather()
    ensure_series_for_demo_day()
    series=st.session_state["fitbit_series_today"]
    peaks=st.session_state["fitbit_peaks_today"]
    high=peaks>4
    st.markdown(f"<div class='wrapper'><div class='hello'>Good Morning, friend</div>"
                f"<div class='sub'>{now_local().strftime('%a %d %b')} · "
                f"{st.session_state['weather']['precip']:.1f}mm · wind "
                f"{st.session_state['weather']['wind']:.1f}m/s</div></div>",unsafe_allow_html=True)
    st.markdown("<div class='wrapper'><div class='block'><div class='badge'>Stress Overview</div>"
                "<div class='stress-visual-wrap'><div class='stress-visual'><div class='viz-label'>Demo stress (Fitbit-like)</div>"
                f"{svg_tag(series_svg(series))}</div></div>"
                f"<div class='sub' style='text-align:center;'>Peaks today: <b>{peaks}</b></div></div></div>",unsafe_allow_html=True)
    msg="High stress detected — a break is recommended" if high else "No excess stress — you can still schedule a break"
    st.markdown(f"<div class='wrapper'><div class='badge'>{msg}</div></div>",unsafe_allow_html=True)
    col1,col2=st.columns(2)
    if col1.button("Suggest break",use_container_width=True):
        favs=st.session_state["favorite_activities"]
        if favs:
            act,_=bandit_choose(favs)
            st.session_state["last_recommendation"]={"activity":act}
            st.session_state["page"]="rec"; st.rerun()
    if col2.button("Next day ▶",use_container_width=True):
        st.session_state["demo_day_offset"]+=1
        st.session_state["fitbit_series_today_key"]=None
        st.session_state["last_recommendation"]=None
        st.rerun()
    render_footer()

def page_rec():
    rec=st.session_state.get("last_recommendation")
    if not rec: st.session_state["page"]="home"; st.rerun(); return
    act=rec["activity"]
    st.markdown(f"<div class='wrapper'><div class='rec-card'><div class='rec-title'>Recommended break</div>"
                f"<div class='kv'><span>Activity</span><span>{act}</span></div>"
                f"<div class='explain'>{expectation_text(act)}</div></div></div>",unsafe_allow_html=True)
    col1,col2=st.columns(2)
    if col1.button("Accept",use_container_width=True):
        st.session_state["page"]="accept"; st.rerun()
    if col2.button("Back",use_container_width=True):
        st.session_state["page"]="home"; st.rerun()
    render_footer()

def page_accept():
    rec=st.session_state["last_recommendation"]; act=rec["activity"]
    dur=st.slider("Duration (minutes)",15,60,st.session_state["accept_duration"],step=5)
    st.session_state["accept_duration"]=dur
    start=now_local().replace(hour=randint(8,20),minute=0)
    st.markdown(f"<div class='wrapper'><div class='rec-card'><div class='rec-title'>Start your break</div>"
                f"<div class='kv'><span>Activity</span><span>{act}</span></div>"
                f"<div class='kv'><span>Start</span><span>{start.strftime('%H:%M')}</span></div>"
                f"<div class='kv'><span>Duration</span><span>{dur} min</span></div></div></div>",unsafe_allow_html=True)
    if st.button("I did this break",use_container_width=True):
        st.session_state["page"]="after"; st.rerun()
    render_footer()

def page_after():
    rec=st.session_state["last_recommendation"]; act=rec["activity"]
    st.markdown(f"<div class='wrapper'><div class='card'><div class='badge'>Feedback</div>"
                f"<div class='h1'>How did it go?</div><p class='p'>Tell me how the break changed your stress.</p>"
                f"<div class='p'>Activity: <b>{act}</b></div></div></div>",unsafe_allow_html=True)
    delta=st.slider("Stress change (−5 much worse → +5 much better)",-5,5,1)
    exp=st.slider("Experience rating (1–10)",1,10,7)
    if st.button("Next day ▶",use_container_width=True):
        bandit_update(act,delta,exp)
        st.session_state["last_recommendation"]=None
        st.session_state["demo_day_offset"]+=1
        st.session_state["fitbit_series_today_key"]=None
        st.session_state["page"]="home"; st.rerun()
    render_footer()

# ──────────────────────────────────────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────────────────────────────────────
pg=st.session_state["page"]
if pg=="initial": page_initial()
elif pg=="home": page_home()
elif pg=="rec": page_rec()
elif pg=="accept": page_accept()
else: page_after()