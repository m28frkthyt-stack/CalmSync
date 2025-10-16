# app.py
import streamlit as st
import requests
import math
import time
import urllib.parse
from random import randint, random
from datetime import datetime, timedelta, timezone

APP_VERSION = "v1.0.1"

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
.badge{background:#ffd7e6;color:#8a3a5b;border-radius:999px;padding:6px 12px;font-weight:600;display:inline-block}

/* White pill buttons */
.stButton>button{
  background:#fff !important;color:var(--ink) !important;border:1px solid var(--border) !important;
  border-radius:999px !important;padding:10px 14px !important;font-weight:700 !important;font-size:.98rem !important;margin:8px 6px !important;
}
.actions .stButton>button{width:100% !important;margin:10px 0 22px 0 !important}

/* Stress graph centered */
.stress-visual-wrap{display:flex;justify-content:center;align-items:center;margin-top:18px;width:100%}
.stress-visual{max-width:320px;width:100%;background:#fff;border-radius:14px;border:1px solid var(--border);padding:12px;box-sizing:border-box;text-align:center}
.viz-label{font-size:.8rem;color:#7f5b69;background:#fff0f6;border:1px solid var(--border);padding:3px 10px;border-radius:999px;margin-bottom:8px;display:inline-block}

/* Key/Value lines */
.kv{display:flex;justify-content:space-between;gap:12px;margin:8px 0}
.kv span:first-child{color:#7f5b69}.kv span:last-child{font-weight:600;color:#492635}

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
    ss.setdefault("demo_day_offset",0); ss.setdefault("fitbit_series_today",None); ss.setdefault("fitbit_peaks_today",0); ss.setdefault("fitbit_series_today_key",None)
    for a in ss["all_activities"]:
        ss["model"]["overall"].setdefault(a,{"n":0,"value":0.0})

ss_init()
inject_css()

# ──────────────────────────────────────────────────────────────────────────────
# Utils
# ──────────────────────────────────────────────────────────────────────────────
def now_local()->datetime:
    return datetime.now()+timedelta(days=st.session_state.get("demo_day_offset",0))

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

def _to_local(dt):
    if getattr(dt,"tzinfo",None) is None: return dt
    return datetime.fromtimestamp(dt.timestamp())
def _parse_ics_dt(s):
    s=s.strip()
    if len(s)==8 and s.isdigit():
        d=datetime.strptime(s,"%Y%m%d"); start=d.replace(hour=0,minute=0,second=0,microsecond=0); return ("allday",start,start+timedelta(days=1))
    if s.endswith("Z"): return _to_local(datetime.strptime(s,"%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc))
    try: return datetime.strptime(s,"%Y%m%dT%H%M%S")
    except: return None
def _unfold_ics_lines(text):
    out=[]; 
    for line in text.splitlines():
        if line.startswith(" ") and out: out[-1]+=line[1:]
        else: out.append(line)
    return out
def parse_ics(text):
    evs=[]; lines=_unfold_ics_lines(text); in_ev=False; dtstart=dtend=transp=busystatus=None
    for ln in lines:
        if ln.startswith("BEGIN:VEVENT"): in_ev=True; dtstart=dtend=transp=busystatus=None; continue
        if ln.startswith("END:VEVENT"):
            if in_ev and dtstart:
                stp=_parse_ics_dt(dtstart); enp=_parse_ics_dt(dtend) if dtend else None
                if isinstance(stp,tuple): start, end = stp[1], (enp[1] if isinstance(enp,tuple) else stp[2])
                else: start, end = stp, (enp if enp else stp+timedelta(hours=1))
                tr=(transp or "").upper().strip(); bs=(busystatus or "").upper().strip(); busy=not (tr=="TRANSPARENT" or bs=="FREE")
                if start and end and end>start: evs.append({"start":start,"end":end,"busy":busy})
            in_ev=False; continue
        if not in_ev: continue
        if ln.startswith("DTSTART"): dtstart=ln.split(":",1)[-1]
        elif ln.startswith("DTEND"): dtend=ln.split(":",1)[-1]
        elif ln.startswith("TRANSP"): transp=ln.split(":",1)[-1]
        elif "BUSYSTATUS" in ln: busystatus=ln.split(":",1)[-1]
    return evs
def fetch_and_cache_calendar(url:str):
    if not url.strip():
        st.session_state["calendar_last_status"]=""; st.session_state["calendar_events"]=[]; return
    try:
        r=requests.get(url,timeout=10); r.raise_for_status()
        events=parse_ics(r.text); nowr=datetime.now(); horizon=nowr+timedelta(days=30)
        busy=[(e["start"],e["end"]) for e in events if e.get("busy",True) and not (e["end"]<nowr or e["start"]>horizon)]
        busy.sort(key=lambda x:x[0]); st.session_state["calendar_events"]=busy; st.session_state["calendar_last_status"]=f"Loaded {len(busy)} busy events."
    except Exception as ex:
        st.session_state["calendar_events"]=[]; st.session_state["calendar_last_status"]=f"Failed to load calendar: {ex}"
def overlaps_busy(start_dt,end_dt):
    for s,e in st.session_state.get("calendar_events",[]): 
        if start_dt<e and end_dt>s: return True
    return False
def list_available_slots(day_dt,duration_min):
    day=day_dt.date(); earliest=datetime.combine(day,datetime.min.time()).replace(hour=8); latest=datetime.combine(day,datetime.min.time()).replace(hour=22)
    step=timedelta(minutes=30); dur=timedelta(minutes=duration_min); real_today=datetime.now().date(); cutoff=earliest if day!=real_today else datetime.now()
    slots=[]; cur=earliest
    while cur+dur<=latest:
        ok=not overlaps_busy(cur,cur+dur)
        if day!=real_today:
            if ok: slots.append(cur)
        else:
            if cur>=cutoff and ok: slots.append(cur)
        cur+=step
    return slots
def make_ics(summary,start_dt,duration_min,description=""):
    end_dt=start_dt+timedelta(minutes=duration_min); fmt=lambda dt: dt.strftime("%Y%m%dT%H%M%S")
    uid=f"{int(time.time())}-{abs(hash(summary))}@stress-aware"; desc=description.replace("\\n"," ")
    ics=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//StressAware//BreakScheduler//EN","BEGIN:VEVENT",
         f"UID:{uid}",f"DTSTAMP:{fmt(now_local())}",f"DTSTART:{fmt(start_dt)}",f"DTEND:{fmt(end_dt)}",
         f"SUMMARY:{summary}",f"DESCRIPTION:{desc}","END:VEVENT","END:VCALENDAR"]
    return "\n".join(ics).encode("utf-8")

def series_svg(series):
    n=len(series); w,h=288,120; pad=8; mn,mx=min(series),max(series); rng=max(1,mx-mn)
    sx=lambda i: pad+(w-2*pad)*(i/max(1,n-1)); sy=lambda v: pad+(h-2*pad)*(1-(v-mn)/rng)
    pts=" ".join([f"{sx(i):.1f},{sy(v):.1f}" for i,v in enumerate(series)])
    svg=f"<svg width='{w}' height='{h}' viewBox='0 0 {w} {h}' xmlns='http://www.w3.org/2000/svg' preserveAspectRatio='none'>"
    svg+="<defs><linearGradient id='grad' x1='0' x2='1'><stop offset='0%' stop-color='#ff6aa9'/><stop offset='100%' stop-color='#f9a7c1'/></linearGradient></defs>"
    svg+=f"<polyline points='{pts}' fill='none' stroke='url(#grad)' stroke-width='2'/></svg>"; return svg
def svg_tag(svg):
    return f"<img src='data:image/svg+xml;utf8,{urllib.parse.quote(svg)}' width='288' height='120'/>"

# Demo stress series
def generate_demo_series(peaks,length=48):
    base=randint(38,55); s=[base+5*math.sin(i/5.5)+randint(-3,3) for i in range(length)]
    centers=[]; tries=0
    while len(centers)<peaks and tries<200:
        k=randint(3,length-4); 
        if all(abs(k-c)>=4 for c in centers): centers.append(k)
        tries+=1
    for c in centers:
        for j in range(-2,3):
            idx=max(0,min(length-1,c+j)); s[idx]+=randint(10,25)*(1-abs(j)/3)
    return s
def ensure_series_for_demo_day():
    key=f"series_{st.session_state.get('demo_day_offset',0)}"
    if st.session_state.get("fitbit_series_today_key")==key: return
    peaks=randint(5,7) if random()<0.5 else randint(1,4)
    st.session_state["fitbit_series_today"]=generate_demo_series(peaks); st.session_state["fitbit_peaks_today"]=peaks; st.session_state["fitbit_series_today_key"]=key

# Bandit
def bandit_choose(favs,epsilon=0.05,tau=0.8):
    model=st.session_state["model"]["overall"]; scores=[(a,model.get(a,{"value":0})["value"]) for a in favs]
    if not scores: return None,{}
    if random()<epsilon: 
        a=scores[randint(0,len(scores)-1)][0]; return a,dict(scores)
    m=max(s for _,s in scores); exps=[(a,math.exp((s-m)/max(1e-6,tau))) for a,s in scores]; tot=sum(v for _,v in exps)
    r=random(); cum=0
    for a,v in exps:
        cum+=v/tot
        if r<=cum: return a,dict(scores)
    return exps[-1][0],dict(scores)
def bandit_update(activity,delta_stress,exp_rating):
    stt=st.session_state["model"]["overall"].setdefault(activity,{"n":0,"value":0.0})
    reward=float(delta_stress)+0.2*(float(exp_rating)-5.0); n=stt["n"]+1; stt["value"]+= (reward-stt["value"])/n; stt["n"]=n
def expectation_text(activity):
    s=st.session_state["model"]["overall"].get(activity,{"n":0,"value":0}); n=s["n"]; m=s["value"]
    if n==0: return "I don’t know much about this type of break yet — let’s see how it affects you."
    if m<=-2: return "May increase stress for you; consider alternatives."
    if m<-0.5: return "Tends to feel counterproductive in reducing stress, but you may try once again."
    if m<0.5: return "Mixed results so far, do you want to give it another shot?."
    if m<2.5: return "Expected to provide a gentle relief of stress."
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
        <p class='p'>
          Pick at least three favorite activities. You can also add your own —
          and paste your calendar <b>.ics</b> URL so we avoid clashes.
        </p>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Favorites (multiselect chips)
    ms_key=f"fav_ms_{st.session_state['ms_version']}"
    defaults=st.session_state.get("favorite_activities",[])
    selected=st.multiselect("Your favorite activities",
                            options=st.session_state["all_activities"],
                            default=defaults, label_visibility="collapsed",
                            key=ms_key, help="Tap to toggle. Multiple per row.")

    # Inline add-custom (same section)
    c1,c2=st.columns([0.7,0.3])
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

    # Calendar URL — **single input only**, auto-save & test on change
    st.markdown("<div class='wrapper'><div class='card'>", unsafe_allow_html=True)
    new_url = st.text_input(" ", value=st.session_state.get("calendar_url",""),
                            placeholder="Calendar (.ics) URL",
                            label_visibility="collapsed", key="cal_url_input")
    if new_url != st.session_state.get("calendar_url",""):
        st.session_state["calendar_url"]=new_url.strip()
        fetch_and_cache_calendar(st.session_state["calendar_url"])
    if st.session_state.get("calendar_last_status"):
        st.caption(st.session_state["calendar_last_status"])
    st.markdown("</div></div>", unsafe_allow_html=True)

    # Next
    st.markdown("<div class='wrapper actions'>", unsafe_allow_html=True)
    if st.button("Next →", use_container_width=True):
        if len(selected)<3:
            st.warning("Please select at least three activities.")
        else:
            st.session_state["favorite_activities"]=selected
            st.session_state["page"]="home"; st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)
    render_footer()

def ensure_series():
    st.session_state["weather"]=fetch_weather()
    ensure_series_for_demo_day()

def page_home():
    ensure_series()
    series=st.session_state["fitbit_series_today"]; peaks=st.session_state["fitbit_peaks_today"]; high=peaks>4

    st.markdown(f"<div class='wrapper'><div class='hello'>Good Morning, friend</div>"
                f"<div class='sub'>{now_local().strftime('%a %d %b')} · "
                f"{st.session_state['weather']['precip']:.1f}mm · wind {st.session_state['weather']['wind']:.1f}m/s</div></div>",
                unsafe_allow_html=True)

    st.markdown("<div class='wrapper'><div class='block'><div class='badge'>Stress Overview</div>"
                "<div class='stress-visual-wrap'><div class='stress-visual'><div class='viz-label'>Demo stress (Fitbit-like)</div>"
                f"{svg_tag(series_svg(series))}</div></div>"
                f"<div class='sub' style='text-align:center;'>Peaks today: <b>{peaks}</b></div></div></div>", unsafe_allow_html=True)

    msg="High stress detected — a break is recommended" if high else "No excess stress — you can schedule a break"
    st.markdown(f"<div class='wrapper'><div class='badge'>{msg}</div></div>", unsafe_allow_html=True)

    # Suggest + Refresh
    st.markdown("<div class='wrapper actions'>", unsafe_allow_html=True)
    col1,col2=st.columns(2)
    with col1: go=st.button("Suggest break", use_container_width=True)
    with col2: ref=st.button("Refresh calendar", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    if ref and st.session_state.get("calendar_url"): fetch_and_cache_calendar(st.session_state["calendar_url"]); st.rerun()
    if go:
        favs=st.session_state["favorite_activities"]
        if favs:
            act,_=bandit_choose(favs, st.session_state["epsilon"], st.session_state["tau"])
            st.session_state["last_recommendation"]={"activity":act}; st.session_state["page"]="rec"; st.rerun()
        else: st.info("No favorites saved yet. Go back to add some.")

    # Next day at bottom
    st.markdown("<div class='wrapper actions'>", unsafe_allow_html=True)
    if st.button("Next day ▶", use_container_width=True):
        st.session_state["demo_day_offset"]+=1; st.session_state["fitbit_series_today_key"]=None; st.session_state["last_recommendation"]=None; st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)
    render_footer()

def page_rec():
    rec=st.session_state.get("last_recommendation"); 
    if not rec: st.session_state["page"]="home"; st.rerun(); return
    act=rec["activity"]
    st.markdown(f"<div class='wrapper'><div class='rec-card'><div class='rec-title'>Recommended break</div>"
                f"<div class='kv'><span>Activity</span><span>{act}</span></div>"
                f"<div class='explain'>{expectation_text(act)}</div></div></div>", unsafe_allow_html=True)
    st.markdown("<div class='wrapper actions'>", unsafe_allow_html=True)
    c1,c2,c3=st.columns(3)
    with c1: back=st.button("Back", use_container_width=True)
    with c2: accept=st.button("Accept", use_container_width=True)
    with c3:
        diff=st.button("Suggest different", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)
    if back: st.session_state["page"]="home"; st.rerun()
    if diff:
        favs=st.session_state.get("favorite_activities",[])
        if favs:
            alt=min(0.5, st.session_state["epsilon"]+0.2)
            new_act,_=bandit_choose(favs, epsilon=alt, tau=st.session_state["tau"])
            st.session_state["last_recommendation"]={"activity":new_act}
        st.rerun()
    if accept: st.session_state["page"]="accept"; st.rerun()
    render_footer()

def page_accept():
    rec=st.session_state.get("last_recommendation")
    if not rec: st.session_state["page"]="home"; st.rerun(); return
    act=rec["activity"]
    dur=st.slider("Duration (minutes)",15,60,st.session_state["accept_duration"],step=5); st.session_state["accept_duration"]=dur
    slots=list_available_slots(now_local(), dur)
    if slots:
        labels=[s.strftime("%H:%M") for s in slots]
        idx=0
        if st.session_state.get("accept_start_choice") in slots: idx=slots.index(st.session_state["accept_start_choice"])
        chosen_label=st.selectbox("Start time", options=labels, index=idx); start=slots[labels.index(chosen_label)]
        st.session_state["accept_start_choice"]=start
    else:
        start=None; st.info("No free whole/half-hour slots today between 08:00–22:00.")

    st.markdown(f"<div class='wrapper'><div class='rec-card'><div class='rec-title'>Start your break</div>"
                f"<div class='kv'><span>Activity</span><span>{act}</span></div>"
                f"<div class='kv'><span>Start</span><span>{start.strftime('%H:%M') if start else '—'}</span></div>"
                f"<div class='kv'><span>Duration</span><span>{dur} min</span></div></div></div>", unsafe_allow_html=True)

    st.markdown("<div class='wrapper actions'>", unsafe_allow_html=True)
    col1,col2,col3=st.columns(3)
    with col1: did=st.button("I did this break", use_container_width=True, disabled=(start is None))
    if start:
        ics=make_ics(f"Break: {act}", start, dur, "Suggested by Stress-Aware Scheduler.")
        with col2: st.download_button("Plan (.ics)", data=ics, file_name=f"break_{act.replace(' ','_')}.ics", mime="text/calendar", use_container_width=True)
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
    st.markdown(f"<div class='wrapper'><div class='card'><div class='badge'>Feedback</div>"
                f"<div class='h1'>How did it go?</div><p class='p'>Tell me how the break changed your stress.</p>"
                f"<div class='p'>Activity: <b>{act}</b></div></div></div>", unsafe_allow_html=True)
    delta=st.slider("Stress change (−5 much worse → +5 much better)",-5,5,1); exp=st.slider("Experience rating (1–10)",1,10,7)
    st.markdown("<div class='wrapper actions'>", unsafe_allow_html=True)
    if st.button("Next day ▶", use_container_width=True):
        bandit_update(act,delta,exp); st.session_state["last_recommendation"]=None
        st.session_state["demo_day_offset"]+=1; st.session_state["fitbit_series_today_key"]=None
        st.session_state["page"]="home"; st.rerun()
    st.markdown("</div>", unsafe_allow_html=True); render_footer()

# Router
pg=st.session_state["page"]
if pg=="initial": page_initial()
elif pg=="home": page_home()
elif pg=="rec": page_rec()
elif pg=="accept": page_accept()
else: page_after()
