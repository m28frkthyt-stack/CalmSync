# app.py
import streamlit as st
import requests
import math
import time
import urllib.parse
from random import randint, random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo  # timezone correctness

APP_VERSION = "v1.0.6"
TZ = ZoneInfo("Europe/Amsterdam")
SMA_THRESHOLD = 50  # trigger a break when SMA < 50

st.set_page_config(page_title="Stress-Aware Break Scheduler", page_icon="🧠", layout="centered")

# ──────────────────────────────────────────────────────────────────────────────
# CSS — soft pastel pink everywhere + white buttons + chip styling
# ──────────────────────────────────────────────────────────────────────────────
def read_css_file(name: str) -> str:
    try:
        with open(name, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

PASTEL_OVERLAY = """
:root{
  --bg:#ffeef6; --ink:#432838; --ink-sub:#5a3f4a; --border:#f7c2d6;
}
html,body,.stApp,[data-testid="stAppViewContainer"], .stAppViewContainer, .main, [data-testid="stHeader"]{
  background:var(--bg) !important;
}
[data-testid="stHeader"]{backdrop-filter:none !important; background:transparent !important;}

/* Centered mobile layout */
.wrapper{max-width:340px;margin:0 auto;padding:0 10px;}
.card,.block,.rec-card{
  background:#ffffffcc !important;border-radius:18px !important;
  box-shadow:0 8px 30px rgba(255,106,169,.15) !important;
  padding:22px 18px !important;margin-top:28px !important;
}
.h1,.rec-title,.hello{color:var(--ink);font-weight:700;line-height:1.3;margin-bottom:8px;font-size:1.25rem;}
.p,.sub{color:var(--ink-sub);line-height:1.55;font-size:1rem;}
.p.lead-space{margin-bottom:14px;}  /* extra spacing under step-1 explanation */

.badge{background:#ffd7e6;color:#8a3a5b;border-radius:999px;padding:6px 12px;font-weight:600;display:inline-block}

/* White pill buttons */
.stButton>button{
  background:#fff !important;color:var(--ink) !important;border:1px solid var(--border) !important;
  border-radius:999px !important;padding:10px 14px !important;font-weight:700 !important;font-size:.98rem !important;margin:8px 6px !important;
}
.actions .stButton>button{width:100% !important;margin:10px 0 22px 0 !important}

/* Inputs */
div[data-baseweb="input"]>div{border-radius:14px;border:1px solid var(--border)}

/* Visual wrapper */
.stress-visual-wrap{display:flex;justify-content:center;align-items:center;margin-top:18px;width:100%}
.stress-visual{max-width:320px;width:100%;background:#fff;border-radius:14px;border:1px solid var(--border);padding:12px;box-sizing:border-box;text-align:center}
.viz-label{font-size:.8rem;color:#7f5b69;background:#fff0f6;border:1px solid var(--border);padding:3px 10px;border-radius:999px;margin-bottom:8px;display:inline-block}

/* Key/Value lines */
.kv{display:flex;justify-content:space-between;gap:12px;margin:8px 0}
.kv span:first-child{color:#7f5b69}.kv span:last-child{font-weight:600;color:#492635}

/* Calendar mini-overview */
.calmini{margin-top:10px;text-align:center}
.calmini .title{font-weight:800;color:#492635;margin-bottom:4px;text-transform:lowercase;}
.calmini .line{font-size:.9rem;color:#5a3f4a;margin:2px 0}
.calmini .when{font-weight:700;color:#492635}

/* Footer */
.footer{text-align:center;color:#8a3a5b;font-size:.85rem;opacity:.9;margin:40px 0 14px}

/* Multiselect -> small white chips */
div[data-baseweb="select"]>div{border-radius:14px;border:1px solid var(--border)}
.stMultiSelect [data-baseweb="tag"]{
  background:#fff !important;border:1px solid var(--border) !important;color:#6d2f4a !important;
  border-radius:999px !important;padding:2px 8px !important;margin:4px !important;font-weight:600 !important;font-size:.85rem !important;
}
"""

def inject_css():
    page = st.session_state.get("page", "initial")
    css = read_css_file("initial.css") if page == "initial" else (
        read_css_file("homepage.css") if page in ("home", "accept", "rec") else read_css_file("afterquestions.css")
    )
    st.markdown(f"<style>{css}\n{PASTEL_OVERLAY}</style>", unsafe_allow_html=True)

def render_footer():
    st.markdown(f"<div class='footer'>{APP_VERSION}</div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────────────
# State
# ──────────────────────────────────────────────────────────────────────────────
def ss_init():
    ss = st.session_state
    ss.setdefault("page","initial")
    ss.setdefault("all_activities",["Walk outside","Stretch","Breathe 4-7-8","Tea break","Power nap","Quick tidy-up","Listen to calm track"])
    ss.setdefault("favorite_activities",[])
    ss.setdefault("ms_version",0)
    ss.setdefault("model",{"overall":{}})
    ss.setdefault("last_recommendation",None)
    ss.setdefault("epsilon",0.05); ss.setdefault("tau",0.8)
    ss.setdefault("calendar_url",""); ss.setdefault("calendar_events",[]); ss.setdefault("calendar_last_status","")
    ss.setdefault("accept_duration",15); ss.setdefault("accept_start_choice",None)
    ss.setdefault("demo_day_offset",0)
    ss.setdefault("sma_today", None)           # SMA drives trigger
    ss.setdefault("fitbit_series_today_key",None)  # cache key
    for a in ss["all_activities"]:
        ss["model"]["overall"].setdefault(a,{"n":0,"value":0.0})

ss_init()
inject_css()

# ──────────────────────────────────────────────────────────────────────────────
# Utils (timezone-correct)
# ──────────────────────────────────────────────────────────────────────────────
def now_local()->datetime:
    return datetime.now(TZ) + timedelta(days=st.session_state.get("demo_day_offset",0))

EHV_LAT,EHV_LON=51.4416,5.4697
def fetch_weather():
    try:
        url=("https://api.open-meteo.com/v1/forecast?"
             f"latitude={EHV_LAT}&longitude={EHV_LON}&hourly=precipitation,wind_speed_10m&timezone=Europe%2FAmsterdam")
        r=requests.get(url,timeout=7).json()
        times=r["hourly"]["time"]; precip=r["hourly"]["precipitation"]; wind=r["hourly"]["wind_speed_10m"]
        iso=now_local().strftime("%Y-%m-%dT%H:00:00"); idx=times.index(iso) if iso in times else (len(times)-1)
        return {"precip":float(precip[idx]),"wind":float(wind[idx])}
    except Exception:
        return {"precip":0.0,"wind":3.0}

def _to_amsterdam(dt: datetime) -> datetime:
    if dt.tzinfo is None: return dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)

def _parse_ics_dt(val: str):
    s = val.strip()
    if len(s) == 8 and s.isdigit():
        day = datetime.strptime(s, "%Y%m%d").replace(tzinfo=TZ)
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return ("allday", start, end)
    if s.endswith("Z"):
        dt = datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ)
    try:
        dt = datetime.strptime(s, "%Y%m%dT%H%M%S").replace(tzinfo=TZ)
        return dt
    except Exception:
        return None

def _unfold_ics_lines(text: str):
    out=[]
    for line in text.splitlines():
        if line.startswith(" ") and out: out[-1]+=line[1:]
        else: out.append(line)
    return out

def parse_ics(text: str):
    evs=[]; lines=_unfold_ics_lines(text); in_ev=False
    dtstart=dtend=transp=busystatus=summary=None
    for ln in lines:
        if ln.startswith("BEGIN:VEVENT"):
            in_ev=True; dtstart=dtend=transp=busystatus=summary=None; continue
        if ln.startswith("END:VEVENT"):
            if in_ev and dtstart:
                stp=_parse_ics_dt(dtstart); enp=_parse_ics_dt(dtend) if dtend else None
                if isinstance(stp,tuple):
                    start, end = stp[1], (enp[1] if isinstance(enp,tuple) else stp[2])
                else:
                    start, end = stp, (enp if enp else (_to_amsterdam(stp)+timedelta(hours=1)))
                tr=(transp or "").upper().strip(); bs=(busystatus or "").upper().strip()
                busy=not (tr=="TRANSPARENT" or bs=="FREE")
                if start and end and end>start:
                    evs.append({"start":_to_amsterdam(start),"end":_to_amsterdam(end),"busy":busy,"summary":(summary or "").strip()})
            in_ev=False; continue
        if not in_ev: continue
        if ln.startswith("DTSTART"): dtstart=ln.split(":",1)[-1]
        elif ln.startswith("DTEND"): dtend=ln.split(":",1)[-1]
        elif ln.startswith("TRANSP"): transp=ln.split(":",1)[-1]
        elif "BUSYSTATUS" in ln:     busystatus=ln.split(":",1)[-1]
        elif ln.startswith("SUMMARY"): summary=ln.split(":",1)[-1]
    return evs

def fetch_and_cache_calendar(url:str):
    if not url.strip():
        st.session_state["calendar_last_status"]=""; st.session_state["calendar_events"]=[]; return
    try:
        r=requests.get(url,timeout=10); r.raise_for_status()
        events=parse_ics(r.text); nowr=now_local(); horizon=nowr+timedelta(days=30)
        busy=[(e["start"],e["end"],e.get("summary","")) for e in events if e.get("busy",True) and not (e["end"]<nowr or e["start"]>horizon)]
        busy.sort(key=lambda x:x[0]); st.session_state["calendar_events"]=busy
        st.session_state["calendar_last_status"]=f"Loaded {len(busy)} busy events."
    except Exception as ex:
        st.session_state["calendar_events"]=[]; st.session_state["calendar_last_status"]=f"Failed to load calendar: {ex}"

def overlaps_busy(start_dt: datetime, end_dt: datetime) -> bool:
    for s,e,_ in st.session_state.get("calendar_events",[]):
        if start_dt < e and end_dt > s: return True
    return False

def list_available_slots(day_dt: datetime, duration_min: int):
    day=day_dt.date()
    earliest=datetime(day.year,day.month,day.day,8,0,0,tzinfo=TZ)
    latest  =datetime(day.year,day.month,day.day,22,0,0,tzinfo=TZ)
    step=timedelta(minutes=30); dur=timedelta(minutes=duration_min)
    real_today = now_local().date()
    cutoff = earliest if day != real_today else now_local()
    slots=[]; cur=earliest
    while cur + dur <= latest:
        if not overlaps_busy(cur, cur + dur):
            if day != real_today or cur >= cutoff:
                slots.append(cur)
        cur += step
    return slots

def make_ics(summary, start_dt, duration_min, description=""):
    end_dt = start_dt + timedelta(minutes=duration_min)
    fmt = lambda dt: dt.strftime("%Y%m%dT%H%M%S")
    uid = f"{int(time.time())}-{abs(hash(summary))}@stress-aware"
    desc = description.replace("\n", " ")
    ics = [
        "BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//StressAware//BreakScheduler//EN","BEGIN:VEVENT",
        f"UID:{uid}", f"DTSTAMP:{fmt(now_local())}", f"DTSTART:{fmt(start_dt)}", f"DTEND:{fmt(end_dt)}",
        f"SUMMARY:{summary}", f"DESCRIPTION:{desc}", "END:VEVENT","END:VCALENDAR"
    ]
    return "\n".join(ics).encode("utf-8")

# ──────────────────────────────────────────────────────────────────────────────
# Visual: SMA gradient bar (pastel red → yellow → green)
# ──────────────────────────────────────────────────────────────────────────────
def sma_gradient_svg(score: int):
    """Return a pastel horizontal gradient bar with a circular marker for SMA (1–100)."""
    w, h = 288, 30
    # clamp score and map to [0, w-16] then offset by radius (8) so the circle stays within
    s = 0 if score is None else max(0, min(int(score), 100))
    x = (w - 16) * s / 100.0
    svg = f"""
    <svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="smaGrad" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0%" stop-color="#ffb3c6"/>
          <stop offset="50%" stop-color="#ffeabf"/>
          <stop offset="100%" stop-color="#b5f5b2"/>
        </linearGradient>
      </defs>
      <rect x="0" y="{h/3:.1f}" width="{w}" height="{h/3:.1f}" rx="8" fill="url(#smaGrad)" />
      <circle cx="{x+8:.1f}" cy="{h/2:.1f}" r="6" fill="#432838" opacity="0.8" />
    </svg>
    """
    return svg

def svg_tag(svg: str, width=288, height=30):
    return f"<img src='data:image/svg+xml;utf8,{urllib.parse.quote(svg)}' width='{width}' height='{height}'/>"

# ──────────────────────────────────────────────────────────────────────────────
# Demo SMA generator (drives break trigger; number not shown)
# ──────────────────────────────────────────────────────────────────────────────
def generate_demo_sma():
    # 40% chance to be under threshold for demo variability
    if random() < 0.4:
        return randint(25, 49)  # low
    return randint(55, 85)     # okay/good

def ensure_today_data():
    key=f"sma_{st.session_state.get('demo_day_offset',0)}"
    if st.session_state.get("fitbit_series_today_key")==key: return
    st.session_state["sma_today"]=generate_demo_sma()
    st.session_state["fitbit_series_today_key"]=key
    st.session_state["weather"]=fetch_weather()

# ──────────────────────────────────────────────────────────────────────────────
# Bandit (epsilon-greedy + softmax), learning from stress delta + experience
# ──────────────────────────────────────────────────────────────────────────────
def bandit_choose(favs,epsilon=0.05,tau=0.8):
    model=st.session_state["model"]["overall"]; scores=[(a,model.get(a,{"value":0})["value"]) for a in favs]
    if not scores: return None,{}
    if random()<epsilon:
        return scores[randint(0,len(scores)-1)][0], dict(scores)
    m=max(s for _,s in scores); exps=[(a,math.exp((s-m)/max(1e-6,tau))) for a,s in scores]; tot=sum(v for _,v in exps)
    r=random(); cum=0
    for a,v in exps:
        cum += v/tot
        if r <= cum: return a, dict(scores)
    return exps[-1][0], dict(scores)

def bandit_update(activity,delta_stress,exp_rating):
    stt=st.session_state["model"]["overall"].setdefault(activity,{"n":0,"value":0.0})
    reward=float(delta_stress)+0.2*(float(exp_rating)-5.0)
    n=stt["n"]+1
    stt["value"] += (reward - stt["value"]) / n
    stt["n"]=n

def expectation_text(activity):
    s=st.session_state["model"]["overall"].get(activity,{"n":0,"value":0}); n=s["n"]; m=s["value"]
    if n==0: return "I don’t know much about this type of break yet — let’s see how it affects your stress."
    if m<=-2: return "May increase stress for you; consider alternatives."
    if m<-0.5: return "Tends to feel counterproductive in reducing stress, but you may try once again."
    if m<0.5: return "Mixed results so far — want to give it another shot?"
    if m<2.5: return "Expected to provide a gentle reduction in stress."
    if m<4: return "Expected to noticeably lower your stress."
    return "Often provides a strong reduction in stress."

# ──────────────────────────────────────────────────────────────────────────────
# Calendar mini-overview
# ──────────────────────────────────────────────────────────────────────────────
def todays_calendar_lines(day_dt: datetime):
    events = st.session_state.get("calendar_events", [])
    if not events: return []
    day = day_dt.date()
    lines = []
    for s, e, title in events:
        if s.date()!=day and e.date()!=day: continue
        stt=s.astimezone(TZ).strftime("%H:%M"); ett=e.astimezone(TZ).strftime("%H:%M")
        label=title if title else "Busy"
        lines.append(f"<span class='when'>{stt}–{ett}</span> · {label}")
    return sorted(lines)[:4]

# ──────────────────────────────────────────────────────────────────────────────
# Pages
# ──────────────────────────────────────────────────────────────────────────────
def page_initial():
    st.markdown("""
    <div class='wrapper'>
      <div class='card'>
        <div class='badge'>Step 1</div>
        <div class='h1'>Let’s create your personal advice!</div>
        <p class='p lead-space'>
          Pick at least three favorite activities. You can also add your own —
          and paste your calendar <b>.ics</b> URL so we avoid clashes.
        </p>
    """, unsafe_allow_html=True)

    # Favorites + Add custom (same card)
    ms_key=f"fav_ms_{st.session_state['ms_version']}"
    defaults=st.session_state.get("favorite_activities",[])
    selected=st.multiselect("Your favorite activities",
                            options=st.session_state["all_activities"],
                            default=defaults, label_visibility="collapsed",
                            key=ms_key, help="Tap to toggle. Multiple per row.")

    c1,c2=st.columns([0.65,0.35])
    with c1:
        new_act=st.text_input(" ", placeholder="Add custom (e.g., Journal 5m)",
                              label_visibility="collapsed", key=f"custom_add_{st.session_state['ms_version']}")
    with c2:
        if st.button("Add", key=f"add_btn_{st.session_state['ms_version']}"):
            if new_act and new_act.strip():
                na=new_act.strip()
                if na not in st.session_state["all_activities"]:
                    st.session_state["all_activities"].append(na)
                    st.session_state["model"]["overall"].setdefault(na,{"n":0,"value":0.0})
                st.session_state["favorite_activities"]=list(dict.fromkeys(selected+[na]))
                st.session_state["ms_version"]+=1
                st.rerun()

    # Calendar URL + Save (same card)
    cal_col1, cal_col2 = st.columns([0.68, 0.32])
    with cal_col1:
        cal_val = st.text_input(" ", value=st.session_state.get("calendar_url",""),
                                placeholder="Calendar (.ics) URL",
                                label_visibility="collapsed", key="cal_url_input")
    with cal_col2:
        if st.button("Save", key="save_cal_btn"):
            st.session_state["calendar_url"] = cal_val.strip()
            fetch_and_cache_calendar(st.session_state["calendar_url"])
    if st.session_state.get("calendar_last_status"):
        st.caption(st.session_state["calendar_last_status"])

    st.markdown("</div></div>", unsafe_allow_html=True)  # close card

    st.markdown("<div class='wrapper actions'>", unsafe_allow_html=True)
    if st.button("Next →", use_container_width=True):
        if len(selected)<3:
            st.warning("Please select at least three activities.")
        else:
            st.session_state["favorite_activities"]=selected
            st.session_state["page"]="home"; st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)
    render_footer()

def page_home():
    # Prepare daily data
    ensure_today_data()

    sma = st.session_state["sma_today"]
    low_sma = (sma is not None and sma < SMA_THRESHOLD)

    st.markdown(f"<div class='wrapper'><div class='hello'>Good Morning, friend</div>"
                f"<div class='sub'>{now_local().strftime('%a %d %b')} · "
                f"{st.session_state['weather']['precip']:.1f}mm · wind {st.session_state['weather']['wind']:.1f}m/s</div></div>",
                unsafe_allow_html=True)

    # SMA gradient bar + calendar mini-overview
    lines = todays_calendar_lines(now_local())
    cal_html = "<div class='line'>No calendar connected</div>" if (not st.session_state.get("calendar_url")) else (
        "<div class='line'>No events today</div>" if not lines else "".join([f"<div class='line'>{ln}</div>" for ln in lines])
    )
    grad_svg = sma_gradient_svg(sma if sma is not None else 50)
    st.markdown("<div class='wrapper'><div class='block'><div class='badge'>Stress Overview</div>"
                "<div class='stress-visual-wrap'><div class='stress-visual'>"
                "<div class='viz-label'>Daily Stress Management Score (demo)</div>"
                f"{svg_tag(grad_svg)}</div></div>"
                f"<div class='calmini'><div class='title'>schedule today</div>{cal_html}</div>"
                "</div></div>", unsafe_allow_html=True)

    msg = ("Your stress management score is low today — a break is recommended"
           if low_sma else
           "Your stress management score looks okay — you can still schedule a break")
    st.markdown(f"<div class='wrapper'><div class='badge'>{msg}</div></div>", unsafe_allow_html=True)

    # Suggest + Refresh
    st.markdown("<div class='wrapper actions'>", unsafe_allow_html=True)
    col1,col2=st.columns(2)
    with col1: go=st.button("Suggest break", use_container_width=True)
    with col2: ref=st.button("Refresh calendar", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    if ref and st.session_state.get("calendar_url"):
        fetch_and_cache_calendar(st.session_state["calendar_url"]); st.rerun()

    if go:
        favs=st.session_state["favorite_activities"]
        if favs:
            act,_=bandit_choose(favs, st.session_state["epsilon"], st.session_state["tau"])
            st.session_state["last_recommendation"]={"activity":act}; st.session_state["page"]="rec"; st.rerun()
        else:
            st.info("No favorites saved yet. Go back to add some.")

    # Next day (bottom)
    st.markdown("<div class='wrapper actions'>", unsafe_allow_html=True)
    if st.button("Next day ▶", use_container_width=True):
        st.session_state["demo_day_offset"]+=1
        st.session_state["fitbit_series_today_key"]=None
        st.session_state["last_recommendation"]=None
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)
    render_footer()

def page_rec():
    rec=st.session_state.get("last_recommendation")
    if not rec: st.session_state["page"]="home"; st.rerun(); return
    act=rec["activity"]

    st.markdown(f"<div class='wrapper'><div class='rec-card'><div class='rec-title'>Recommended break</div>"
                f"<div class='kv'><span>Activity</span><span>{act}</span></div>"
                f"<div class='explain'>{expectation_text(act)}</div></div></div>", unsafe_allow_html=True)

    st.markdown("<div class='wrapper actions'>", unsafe_allow_html=True)
    c1,c2,c3=st.columns(3)
    with c1: back=st.button("Back", use_container_width=True)
    with c2: accept=st.button("Accept", use_container_width=True)
    with c3: diff=st.button("Suggest different", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    if back:
        st.session_state["page"]="home"; st.rerun()
    if diff:
        favs=st.session_state.get("favorite_activities",[])
        if favs:
            alt=min(0.5, st.session_state["epsilon"]+0.2)
            new_act,_=bandit_choose(favs, epsilon=alt, tau=st.session_state["tau"])
            st.session_state["last_recommendation"]={"activity":new_act}
        st.rerun()
    if accept:
        st.session_state["page"]="accept"; st.rerun()
    render_footer()

def page_accept():
    rec=st.session_state.get("last_recommendation")
    if not rec: st.session_state["page"]="home"; st.rerun(); return
    act=rec["activity"]

    dur=st.slider("Duration (minutes)",15,60,st.session_state["accept_duration"],step=5)
    st.session_state["accept_duration"]=dur

    slots=list_available_slots(now_local(), dur)
    if slots:
        labels=[s.strftime("%H:%M") for s in slots]
        idx=0
        if st.session_state.get("accept_start_choice") in slots:
            idx=slots.index(st.session_state["accept_start_choice"])
        chosen_label=st.selectbox("Start time", options=labels, index=idx)
        start=slots[labels.index(chosen_label)]
        st.session_state["accept_start_choice"]=start
    else:
        start=None
        st.info("No free whole/half-hour slots today between 08:00–22:00.")

    st.markdown(f"<div class='wrapper'><div class='rec-card'><div class='rec-title'>Start your break</div>"
                f"<div class='kv'><span>Activity</span><span>{act}</span></div>"
                f"<div class='kv'><span>Start</span><span>{start.astimezone(TZ).strftime('%H:%M') if start else '—'}</span></div>"
                f"<div class='kv'><span>Duration</span><span>{dur} min</span></div></div></div>", unsafe_allow_html=True)

    st.markdown("<div class='wrapper actions'>", unsafe_allow_html=True)
    col1,col2,col3=st.columns(3)
    with col1: did=st.button("I did this break", use_container_width=True, disabled=(start is None))
    if start:
        ics=make_ics(f"Break: {act}", start, dur, "Suggested by Stress-Aware Scheduler.")
        with col2: st.download_button("Plan (.ics)", data=ics, file_name=f"break_{act.replace(' ','_')}.ics",
                                      mime="text/calendar", use_container_width=True)
    else:
        with col2: st.write("")
    with col3: back=st.button("Back", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    rec["start"]=start; rec["duration"]=dur
    if back: st.session_state["page"]="rec"; st.rerun()
    if did and start: st.session_state["page"]="after"; st.rerun()
    render_footer()

def page_after():
    rec=st.session_state.get("last_recommendation"); act=rec["activity"] if rec else "(activity)"

    st.markdown(f"""
    <div class='wrapper'>
      <div class='card'>
        <div class='badge'>Feedback</div>
        <div class='h1'>How did it go?</div>
        <p class='p'>Tell me how the break changed your stress.</p>
        <div class='p' style='text-align:center; margin-top:6px;'>
          <b>Activity:</b> {act}
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Clear, centered stress-change instructions
    st.markdown("<div class='wrapper' style='text-align:center; margin-top:8px;'>"
                "<div class='p'><b>Stress change</b></div>"
                "<div class='sub' style='margin-top:2px;'>"
                "−5 = much higher stress · 0 = no change · +5 = much lower stress"
                "</div></div>", unsafe_allow_html=True)
    delta = st.slider(" ", -5, 5, 0, label_visibility="collapsed")
    exp = st.slider("Experience rating (1–10)", 1, 10, 7)

    st.markdown("<div class='wrapper actions'>", unsafe_allow_html=True)
    if st.button("Next day ▶", use_container_width=True):
        bandit_update(act, delta, exp)
        st.session_state["last_recommendation"]=None
        st.session_state["demo_day_offset"]+=1
        st.session_state["fitbit_series_today_key"]=None
        st.session_state["page"]="home"; st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)
    render_footer()

# Router
pg=st.session_state["page"]
if pg=="initial": page_initial()
elif pg=="home": page_home()
elif pg=="rec": page_rec()
elif pg=="accept": page_accept()
else: page_after()