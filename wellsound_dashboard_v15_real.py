import streamlit as st
import boto3
from datetime import datetime

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(
    page_title="WellSound — Room Finder",
    layout="wide"
)

# ============================================================
# DATA DEFINITIONS
# ============================================================

ROOMS = [
    {"room_id": "001", "room_name": "Moffatt Library"},
    {"room_id": "002", "room_name": "Lin Cafe"},
    {"room_id": "003", "room_name": "Kimberly Room"},
]

# Raw ML labels mapped to user-friendly display strings
LABEL_DISPLAY = {
    "Focus":         "🤫 Best for Deep Work (Quiet)",
    "Collaborative": "🗣️ Collaborative: Good for Team Meetings",
    "Lively":        "☕ Socializing & Break Time",
    "Disruptive":    "❌ Avoid: Unhealthy Sound Quality",
}

# Badge colors per label
LABEL_STYLES = {
    "Focus":         {"bg": "#EAF3DE", "color": "#27500A"},
    "Collaborative": {"bg": "#E6F1FB", "color": "#0C447C"},
    "Lively":        {"bg": "#FAEEDA", "color": "#633806"},
    "Disruptive":    {"bg": "#FCEBEC", "color": "#791F1F"},
}

# Activity filter options mapped to matching aq_label
ACTIVITY_OPTIONS = {
    "🔍 Show All Rooms":    None,
    "💻 Deep Work / Focus": "Focus",
    "🤝 Team Meeting":      "Collaborative",
    "☕ Social":    "Lively",
}

# rms_db → noise level indicator
def noise_indicator(rms_db):
    if rms_db < -40:
        return "🔈 Quiet"
    elif rms_db < -20:
        return "🔉 Moderate"
    elif rms_db < -10:
        return "🔊 Noisy"
    else:
        return "⚠️ Unhealthy"

# Refresh interval — how often the fragment re-runs (seconds)
REFRESH_INTERVAL = 10   # Originally 30s, but moved up to 10s for demo purposes

# ============================================================
# REAL DATA
# ============================================================
# Requires valid AWS credentials. Before running:
#   aws configure
#   aws configure set aws_session_token YOUR_SESSION_TOKEN
#
# Fill in the table name and region below before running.

def get_real_data():
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    table    = dynamodb.Table("wellsound")

    # Scan the full table and keep only the latest record per room_id
    response = table.scan()
    items    = response.get("Items", [])

    # Group by room_id and keep the one with the highest timestamp
    latest = {}
    for item in items:
        rid = item["room_id"]
        if rid not in latest or int(float(item["timestamp"])) > int(float(latest[rid]["timestamp"])):
            latest[rid] = item

    return list(latest.values())


# ============================================================
# S3 HISTORICAL FEED (future state — not implemented for demo)
# ============================================================
# import json
#
# def get_s3_history(room_id, limit=25):
#     s3     = boto3.client("s3", region_name="us-east-1")
#     bucket = "wellsound-historical-data"
#     key    = f"logs/{room_id}/history.json"
#     try:
#         obj  = s3.get_object(Bucket=bucket, Key=key)
#         data = json.loads(obj["Body"].read().decode("utf-8"))
#         return data[-limit:]
#     except Exception:
#         return []
#
# def get_s3_room_profile(room_id):
#     s3     = boto3.client("s3", region_name="us-east-1")
#     bucket = "wellsound-historical-data"
#     key    = f"profiles/{room_id}/profile.json"
#     try:
#         obj = s3.get_object(Bucket=bucket, Key=key)
#         return json.loads(obj["Body"].read().decode("utf-8"))
#     except Exception:
#         return {}


# ============================================================
# RENDERING HELPERS
# ============================================================

def fmt_timestamp(ts):
    return datetime.utcfromtimestamp(ts).strftime("%H:%M:%S UTC")

def render_room_card(col, msg):
    label   = msg["aq_label"]
    style   = LABEL_STYLES.get(label, {"bg": "#eee", "color": "#333"})
    display = LABEL_DISPLAY.get(label, label)
    noise   = noise_indicator(float(msg["rms_db"]))
    pct     = int(float(msg["confidence"]) * 100)

    with col:
        st.markdown(
            f'<div style="background:{style["bg"]};border:1px solid {style["bg"]};border-radius:14px;padding:1.2rem 1.3rem;">'
            f'<div style="font-size:11px;font-family:monospace;color:{style["color"]};opacity:0.7;margin-bottom:4px">id:{msg["room_id"]}</div>'
            f'<div style="font-size:17px;font-weight:600;color:#1a1a1a;margin-bottom:12px">{msg["room_name"]}</div>'
            f'<div style="font-size:20px;font-weight:700;color:{style["color"]};margin-bottom:6px">{display}</div>'
            f'<div style="font-size:12px;color:#555;margin-bottom:6px">{noise}</div>'
            f'<div style="font-size:12px;font-family:monospace;color:#777">{msg["rms_db"]} dB</div>'
            f'</div>',
            unsafe_allow_html=True
        )
        with st.expander("Acoustic details"):
            st.markdown(f"**Activity match:** {display}")
            st.markdown(f"**Confidence:** {pct}%")
            st.progress(pct / 100)
            st.markdown(f"**Spectral Centroid:** {msg.get('centroid', '—')} Hz")
            st.markdown(f"**Speech Ratio:** {round(float(msg.get('speech_ratio', 0)) * 100)}%")
            st.markdown(f"**Low Ratio:** {round(float(msg.get('low_ratio', 0)) * 100)}%")
            st.markdown(f"**Mid Ratio:** {round(float(msg.get('mid_ratio', 0)) * 100)}%")
            st.markdown(f"**High Ratio:** {round(float(msg.get('high_ratio', 0)) * 100)}%")


# ============================================================
# SIDEBAR — ACTIVITY FILTER
# Rendered once — stays completely still between refreshes
# ============================================================

st.sidebar.markdown("## 🎯 What do you need?")
st.sidebar.markdown("Select an activity to find the best room for you.")

selected_activity = st.sidebar.radio(
    label="Activity",
    options=list(ACTIVITY_OPTIONS.keys()),
    label_visibility="collapsed"
)

target_label = ACTIVITY_OPTIONS[selected_activity]

st.sidebar.divider()
st.sidebar.caption("Room status refreshes every 30 seconds.")


# ============================================================
# MAIN HEADER
# Rendered once — stays completely still between refreshes
# ============================================================

st.markdown("## 🔊 WellSound — Room Finder")
st.caption("Live acoustic quality status · Helping you find the right space")
st.divider()

st.markdown(
    '<div style="border-left:3px solid #1D9E75;padding:0.6rem 1rem;margin-bottom:1rem">'
    '<p style="font-size:15px;font-weight:600;color:#1a1a1a;margin-bottom:4px">Finding your ideal space</p>'
    '<p style="font-size:14px;color:#444;margin:0">Use the sidebar to filter rooms by what you\'re planning to do. '
    'Each card shows the current sound quality for that room. '
    'If a room shows <strong>❌ Avoid: Unhealthy Sound Quality</strong>, steer clear regardless of its usual status — '
    'conditions update automatically, so check back if a room becomes available.</p>'
    '</div>',
    unsafe_allow_html=True
)


# ============================================================
# LIVE SECTION — refreshes every 30 seconds via st.fragment
# Only this section re-runs on each cycle. The sidebar,
# header, and info blurb above stay completely untouched.
# ============================================================

@st.fragment(run_every=REFRESH_INTERVAL)
def live_section():
    # Fetch live data from DynamoDB
    messages = get_real_data()

    # Initialise feed in session state
    if "feed" not in st.session_state:
        st.session_state.feed = []

    # Filter by selected activity
    if target_label is None:
        filtered = messages
    else:
        filtered = [m for m in messages if m["aq_label"] == target_label]

    # Render room cards
    if target_label is None:
        st.markdown(f"**Showing all {len(messages)} room(s)**")
    else:
        st.markdown(f"**{selected_activity} — {len(filtered)} matching room(s)**")

    if filtered:
        cols = st.columns(len(filtered))
        for col, msg in zip(cols, filtered):
            render_room_card(col, msg)
    else:
        st.markdown(
            '<div style="text-align:center;padding:2rem;color:#999;font-size:15px;">'
            '😔 Oops! All rooms are currently busy. Try a different activity.'
            '</div>',
            unsafe_allow_html=True
        )

    # Update historical feed — only log if this room+timestamp combo isn't already in the feed
    already_logged = {(e["room_id"], e["timestamp"]) for e in st.session_state.feed}
    for msg in messages:
        ts = int(float(msg["timestamp"]))
        if (msg["room_id"], ts) not in already_logged:
            st.session_state.feed.insert(0, {
                "room_id":   msg["room_id"],
                "room_name": msg["room_name"],
                "aq_label":  msg["aq_label"],
                "rms_db":    float(msg["rms_db"]),
                "timestamp": ts,
            })
            already_logged.add((msg["room_id"], ts))
    st.session_state.feed = st.session_state.feed[:25]
    st.session_state.feed = st.session_state.feed[:25]

    # Render historical feed
    st.divider()
    st.markdown(
        '<div style="font-size:11px;font-family:monospace;color:#aaa;'
        'letter-spacing:0.06em;margin-bottom:6px">HISTORICAL FEED — last 25 messages</div>',
        unsafe_allow_html=True
    )
    for entry in st.session_state.feed:
        noise = noise_indicator(float(entry["rms_db"]))
        st.markdown(
            f'<div style="font-size:11px;font-family:monospace;color:#888;'
            f'line-height:1.8;border-bottom:0.5px solid #f0f0f0;padding:2px 0">'
            f'<span style="color:#bbb">[{fmt_timestamp(entry["timestamp"])}]</span> '
            f'<span style="color:#aaa">id:{entry["room_id"]}</span> · '
            f'<span style="color:#1a1a1a;font-weight:500">{entry["room_name"]}</span> · '
            f'<span style="color:#1a1a1a">{entry["aq_label"]}</span> · '
            f'{noise}'
            f'</div>',
            unsafe_allow_html=True
        )

# Call the fragment — Streamlit handles the refresh timing automatically
live_section()
