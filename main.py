"""
Wade Live 2.1 (Gunicorn-Compatible Flask Edition)
- Flask server runs on / (required by Render)
- Wade bot runs in background thread
"""

import os
import time
import datetime
import requests
import json
import threading
from flask import Flask
from openai import OpenAI
from atproto import Client

# === CONFIGURATION ===

SLEEP_INTERVAL = 60  # Check every 60 seconds
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

# Player priorities
priority_players = {
    "Jung Hoo Lee": {"hits": True, "walks": True, "steals": True},
    "Matt Chapman": {"xbh": True},
    "Tyler Fitzgerald": {"hits": True},
    "Willy Adames": {"xbh": True}
}

processed_play_ids = set()

# === FUNCTIONS ===

def is_giants_game_today(schedule):
    today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    for game in schedule:
        if game.get("date") == today and not game.get("off_day", False):
            return True
    return False

def get_game_id():
    today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
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

# === BACKGROUND TASK ===

def wade_loop():
    print("🤖 Wade Live 2.1 (Gunicorn Edition) initialised...")

    while True:
        try:
            if not is_giants_game_today(giants_schedule):
                print("📆 No Giants game today. Sleeping...")
                time.sleep(SLEEP_INTERVAL)
                continue

            game_id = get_game_id()
            if not game_id:
                print("❌ No Giants game found today. Sleeping...")
                time.sleep(SLEEP_INTERVAL)
                continue

            print(f"📺 Monitoring Giants Game ID: {game_id}")
            
            while True:
                plays = fetch_all_plays(game_id)
                print(f"🔍 Fetched {len(plays)} plays...")

                for play in plays:
                    play_id = play.get("playId")
                    if not play_id or play_id in processed_play_ids:
                        continue

                    processed_play_ids.add(play_id)

                    batter = play.get("matchup", {}).get("batter", {}).get("fullName", "Unknown")
                    event = play.get("result", {}).get("event", "Unknown")
                    desc = play.get("result", {}).get("description", "")
                    inning = play.get("about", {}).get("inning", "?")
                    half = "T" if play.get("about", {}).get("halfInning") == "top" else "B"

                    decision, reason = should_post(play)

                    print(f"📃 [{inning}{half}] {batter} — {event.upper()} — Reason: {reason}")

                    if decision:
                        print("📢 Trigger matched. Generating post...")
                        post = generate_post(desc)
                        print(f"📤 Posting: {post}")
                        client_bsky.send_post(text=post)
                        log_post(post)

                time.sleep(SLEEP_INTERVAL)

        except Exception as e:
            print(f"❌ ERROR: {e}")
            time.sleep(SLEEP_INTERVAL)

# === START BOT THREAD WHEN APP IS IMPORTED ===

threading.Thread(target=wade_loop, daemon=True).start()
