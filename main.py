"""
Wade Live 2.6 – Styled HTML Log + Debug
"""

import os
import time
import datetime
import requests
import json
import threading
from flask import Flask, jsonify, render_template_string
from openai import OpenAI
from atproto import Client
from zoneinfo import ZoneInfo

# === CONFIGURATION ===

SLEEP_INTERVAL = 60
TEAM_ID = 137  # San Francisco Giants

# Load environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE")
BLUESKY_PASSWORD = os.getenv("BLUESKY_PASSWORD")

# Clients
client_ai = OpenAI(api_key=OPENAI_API_KEY)
client_bsky = Client()
client_bsky.login(BLUESKY_HANDLE, BLUESKY_PASSWORD)

# Load Wade's prompt
with open("wade_prompt.txt", "r", encoding="utf-8") as f:
    WADE_PROMPT = f.read()

# Load Giants schedule
try:
    with open("giants_schedule.json", "r", encoding="utf-8") as f:
        giants_schedule = json.load(f)
except Exception as e:
    print(f"❌ Could not load Giants schedule: {e}")
    giants_schedule = []

# === GLOBAL STATE TRACKERS ===

processed_play_ids = set()
current_game_id = None
latest_play_count = 0
posts_made = 0
log_lines = []

# === PLAYER PRIORITIES ===

priority_players = {
    "Jung Hoo Lee": {"hits": True, "walks": True, "steals": True},
    "Matt Chapman": {"xbh": True},
    "Tyler Fitzgerald": {"hits": True},
    "Willy Adames": {"xbh": True}
}

# === FUNCTIONS ===

def is_giants_game_today(schedule):
    today_pacific = datetime.datetime.now(ZoneInfo("America/Los_Angeles")).date()
    for game in schedule:
        try:
            start_time = datetime.datetime.fromisoformat(game["start_time_utc"].replace("Z", "+00:00"))
            start_time_pacific = start_time.astimezone(ZoneInfo("America/Los_Angeles"))
            if start_time_pacific.date() == today_pacific:
                log_lines.append(f"✅ Giants game today at {start_time_pacific.strftime('%I:%M %p PT')}")
                return True
        except Exception as e:
            log_lines.append(f"⚠️ Error parsing game time: {e}")
    return False

def get_game_id():
    today = datetime.datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule/games/?sportId=1&date={today}"
    response = requests.get(url).json()
    for date in response.get("dates", []):
        for game in date.get("games", []):
            if TEAM_ID in [game["teams"]["home"]["team"]["id"], game["teams"]["away"]["team"]["id"]]:
                return game["gamePk"]
    return None

def fetch_all_plays(game_id):
    url = f"https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live"
    return requests.get(url).json().get("liveData", {}).get("plays", {}).get("allPlays", [])

def is_giants_pa(play):
    batter = play.get("matchup", {}).get("batter", {}).get("fullName", "").lower()
    giants = ["jung hoo lee", "matt chapman", "wilmer flores", "patrick bailey", "michael conforto", "tyler fitzgerald", "heliot ramos", "spencer huff"]
    team_id = (
        play.get("team", {}).get("id") or
        play.get("matchup", {}).get("battingTeam", {}).get("id")
    )
    return team_id == TEAM_ID or batter in giants

def should_post(play):
    event = play.get("result", {}).get("event", "")
    desc = play.get("result", {}).get("description", "")
    batter = play.get("matchup", {}).get("batter", {}).get("fullName", "")
    rbi = play.get("result", {}).get("rbi", 0)

    if not event or not desc or event.lower() == "pending":
        return False, "No event/description yet"

    if event == "Home Run" and is_giants_pa(play):
        return True, "Giants Home Run"

    if is_giants_pa(play) and rbi > 0:
        return True, "Giants RBI scoring play"

    if batter in priority_players:
        if batter == "Jung Hoo Lee":
            if event in {"Single", "Double", "Triple", "Home Run", "Walk", "Hit By Pitch"}:
                return True, f"Priority: Jung Hoo Lee {event}"
            if event == "Stolen Base":
                return True, "Priority: Jung Hoo Lee Stolen Base"
        elif batter == "Matt Chapman":
            if event in {"Double", "Triple", "Home Run"}:
                return True, f"Priority: Matt Chapman {event}"
        elif batter == "Tyler Fitzgerald":
            if event in {"Single", "Double", "Triple", "Home Run"}:
                return True, f"Priority: Tyler Fitzgerald {event}"
        elif batter == "Willy Adames":
            if event in {"Double", "Triple", "Home Run"}:
                return True, f"Priority: Willy Adames {event}"

    return False, "No posting condition met"

def generate_post(description):
    messages = [
        {"role": "system", "content": WADE_PROMPT},
        {"role": "user", "content": f"Write a Bluesky post reacting to this: {description}"}
    ]
    response = client_ai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=messages,
        temperature=0.85,
        max_tokens=300
    )
    post = response.choices[0].message.content.strip()
    if "#SFGiants" not in post:
        post += " #SFGiants"
    return post[:300]

def log_post(post_text):
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
    with open("wade_posts_log.txt", "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {post_text}\n")

# === FLASK APP SETUP ===

app = Flask(__name__)

@app.route('/')
def home():
    return "Wade Live is running."

@app.route('/status')
def status():
    today_pacific = datetime.datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    return jsonify({
        "date": today_pacific,
        "game_id": current_game_id,
        "plays_fetched": latest_play_count,
        "posts_made": posts_made,
        "is_game_today": is_giants_game_today(giants_schedule)
    })

@app.route('/log')
def log():
    html = """
    <html><head><title>Wade Debug Log</title>
    <meta http-equiv='refresh' content='10'>
    <style>
    body { font-family: monospace; background: #111; color: #eee; padding: 1em; }
    .play { margin-bottom: 1em; }
    .event { color: #ccc; }
    .timestamp { color: #888; }
    .post { color: #6cf; font-style: italic; }
    .bold { font-weight: bold; }
    </style>
    </head><body>
    """
    for line in log_lines[-200:]:
        if "📤 POSTING:" in line:
            html += f"<div class='play post'>📤 <em>{line.replace('📤 POSTING:', '').strip()}</em></div>"
        elif line.startswith("📃"):
            html += f"<div class='play bold'>{line}</div>"
        elif line.startswith("👀") or line.startswith("✅") or line.startswith("📺"):
            html += f"<div class='play event'>{line}</div>"
        else:
            html += f"<div class='play timestamp'>{line}</div>"
    html += "</body></html>"
    return html

# === BACKGROUND TASK ===

def wade_loop():
    global current_game_id, latest_play_count, posts_made

    log_lines.append("🤖 Wade Live 2.6 (Styled Log Edition) started...")

    while True:
        try:
            if not is_giants_game_today(giants_schedule):
                log_lines.append("📆 No Giants game today. Sleeping...")
                time.sleep(SLEEP_INTERVAL)
                continue

            game_id = get_game_id()
            if not game_id:
                log_lines.append("❌ No Giants game found today. Sleeping...")
                time.sleep(SLEEP_INTERVAL)
                continue

            current_game_id = game_id
            log_lines.append(f"📺 Monitoring Giants Game ID: {game_id}")

            while True:
                plays = fetch_all_plays(game_id)
                latest_play_count = len(plays)
                log_lines.append(f"🔍 Fetched {latest_play_count} plays...")

                for play in plays:
                    play_id = play.get("playId") or play.get("playEndTime") or (
                        f"{play.get('about', {}).get('inning')}-"
                        f"{play.get('about', {}).get('halfInning')}-"
                        f"{play.get('matchup', {}).get('batter', {}).get('id')}-"
                        f"{play.get('result', {}).get('event')}"
                    )

                    log_lines.append(f"👀 Checking play: {play_id} (batter: {play.get('matchup', {}).get('batter', {}).get('fullName', 'Unknown')})")

                    if play_id in processed_play_ids:
                        log_lines.append(f"⏩ Skipping play {play_id} (already processed)")
                        continue

                    processed_play_ids.add(play_id)

                    batter = play.get("matchup", {}).get("batter", {}).get("fullName", "Unknown")
                    event = play.get("result", {}).get("event", "Unknown")
                    desc = play.get("result", {}).get("description", "")
                    inning = play.get("about", {}).get("inning", "?")
                    half = "T" if play.get("about", {}).get("halfInning") == "top" else "B"

                    decision, reason = should_post(play)

                    log_lines.append(f"📃 [{inning}{half}] {batter} — {event.upper()} — Reason: {reason}")

                    if decision:
                        post = generate_post(desc)
                        log_lines.append(f"📤 POSTING: {post}")
                        client_bsky.send_post(text=post)
                        log_post(post)
                        posts_made += 1

                time.sleep(SLEEP_INTERVAL)

        except Exception as e:
            log_lines.append(f"❌ ERROR: {e}")
            time.sleep(SLEEP_INTERVAL)

# === START BACKGROUND THREAD ALWAYS ===

threading.Thread(target=wade_loop, daemon=True).start()

# === START FLASK SERVER LOCALLY ===

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
