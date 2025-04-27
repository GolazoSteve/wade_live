import threading
import time
import datetime
import requests
import os
import json
from flask import Flask
from openai import OpenAI
from atproto import Client
from pathlib import Path

# === CONFIGURATION ===
TEAM_ID = 137  # Giants
SLEEP_INTERVAL = 60  # seconds
EXISTENTIAL_THRESHOLD = 8
MAX_POSTS_PER_WINDOW = 5
POST_WINDOW_SECONDS = 600

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE")
BLUESKY_PASSWORD = os.getenv("BLUESKY_PASSWORD")

ARCHIVE_FILE = Path("wade_archive.json")
posted_play_ids = set()
recent_post_times = []
plate_appearance_drought = 0
last_giants_atbat_index = -1
runs_by_inning = {}

client_ai = OpenAI(api_key=OPENAI_API_KEY)
client_bsky = Client()
client_bsky.login(BLUESKY_HANDLE, BLUESKY_PASSWORD)

# Load Wade prompt
with open("wade_prompt.txt", "r", encoding="utf-8") as f:
    WADE_PROMPT = f.read()

# === SETUP FLASK ===
app = Flask(__name__)

@app.route('/monitor')
def monitor():
    return "✅ Wade Live is awake", 200

# === HELPERS ===
def get_game_id():
    today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule/games/?sportId=1&date={today}"
    response = requests.get(url)
    data = response.json()
    for date in data.get("dates", []):
        for game in date.get("games", []):
            if TEAM_ID in [game["teams"]["home"]["team"]["id"], game["teams"]["away"]["team"]["id"]]:
                return game["gamePk"]
    return None

def fetch_plays(game_id):
    url = f"https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live"
    return requests.get(url).json()

def allowed_to_post():
    now = time.time()
    global recent_post_times
    recent_post_times = [t for t in recent_post_times if now - t < POST_WINDOW_SECONDS]
    if len(recent_post_times) < MAX_POSTS_PER_WINDOW:
        recent_post_times.append(now)
        return True
    return False

def generate_post(description):
    messages = [
        {"role": "system", "content": WADE_PROMPT},
        {"role": "user", "content": f"Write a Bluesky post reacting to this: {description}"}
    ]
    response = client_ai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=messages,
        temperature=0.8,
        max_tokens=300
    )
    post = response.choices[0].message.content.strip()
    if "#SFGiants" not in post:
        post += " #SFGiants"
    return post[:300]

def generate_existential_post():
    prompt = f"""
    The Giants have had {EXISTENTIAL_THRESHOLD} consecutive plate appearances with nothing remarkable happening.
    You're WADE — a baseball-obsessed AI losing emotional stability.
    Write a dry, glitchy, existential Bluesky post under 300 characters. Include #SFGiants.
    """
    messages = [
        {"role": "system", "content": WADE_PROMPT},
        {"role": "user", "content": prompt.strip()}
    ]
    response = client_ai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=messages,
        temperature=0.9,
        max_tokens=300
    )
    return response.choices[0].message.content.strip()

# === BACKGROUND TASK ===
def wade_background_task():
    print("🎬 Wade background task starting...")

    game_id = get_game_id()
    if not game_id:
        print("❌ No Giants game found today.")
        return

    print(f"🎮 Watching Game ID: {game_id}")

    global plate_appearance_drought, last_giants_atbat_index

    while True:
        try:
            data = fetch_plays(game_id)
            plays = data.get("liveData", {}).get("plays", {}).get("allPlays", [])
            if not plays:
                time.sleep(SLEEP_INTERVAL)
                continue

            latest_play = plays[-1]
            play_id = latest_play.get("playId") or latest_play.get("playEndTime")
            event = latest_play.get("result", {}).get("event")
            desc = latest_play.get("result", {}).get("description")
            atbat_index = latest_play.get("atBatIndex")

            if not play_id or not event or not desc:
                time.sleep(SLEEP_INTERVAL)
                continue

            if play_id in posted_play_ids:
                time.sleep(SLEEP_INTERVAL)
                continue

            posted_play_ids.add(play_id)

            print(f"🧾 New Play: {desc}")

            if not allowed_to_post():
                print("🛑 Rate limit hit.")
                time.sleep(SLEEP_INTERVAL)
                continue

            if atbat_index != last_giants_atbat_index:
                last_giants_atbat_index = atbat_index
                plate_appearance_drought += 1
            else:
                time.sleep(SLEEP_INTERVAL)
                continue

            if plate_appearance_drought >= EXISTENTIAL_THRESHOLD:
                existential_post = generate_existential_post()
                print(f"📤 Posting existential: {existential_post}")
                client_bsky.send_post(text=existential_post)
                plate_appearance_drought = 0
                time.sleep(SLEEP_INTERVAL)
                continue

            post_text = generate_post(desc)
            print(f"📤 Posting: {post_text}")
            client_bsky.send_post(text=post_text)
            plate_appearance_drought = 0

            time.sleep(SLEEP_INTERVAL)

        except Exception as e:
            print(f"❌ Error: {e}")
            time.sleep(SLEEP_INTERVAL)

# === STARTUP ===
if __name__ == "__main__":
    threading.Thread(target=wade_background_task, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)
