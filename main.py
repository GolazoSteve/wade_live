"""
Wade Live 2.3 (Test Mode + Live Monitor)
- Test Mode or Live Mode controlled by WADE_TEST_MODE env var
- Flask health server (port 10000)
- /monitor webpage shows live commentary feed
- OpenAI and Bluesky integration
- Safe delayed Bluesky login
"""

import os
import time
import datetime
import threading
import requests
import json
from flask import Flask
from openai import OpenAI
from atproto import Client

# === CONFIGURATION ===

TEST_MODE = os.getenv("WADE_TEST_MODE", "False").lower() == "true"

SLEEP_INTERVAL = 60  # seconds between live polls
TEST_PLAY_DELAY = 2  # seconds between plays in test mode
TEAM_ID = 137  # Giants

# Environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE")
BLUESKY_PASSWORD = os.getenv("BLUESKY_PASSWORD")

# Clients
client_ai = OpenAI(api_key=OPENAI_API_KEY)
client_bsky = None  # Lazy login later

# Commentary memory log for the /monitor page
commentary_log = []

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

priority_players = {
    "Jung Hoo Lee": {"hits": True, "walks": True, "steals": True},
    "Matt Chapman": {"xbh": True},
    "Tyler Fitzgerald": {"hits": True},
    "Willy Adames": {"xbh": True}
}

processed_play_ids = set()

# === FLASK SERVER ===

app = Flask(__name__)

@app.route('/health')
def health_check():
    return "OK", 200

@app.route('/monitor')
def monitor_page():
    html = "<h1>Wade Live Monitor</h1><ul>"
    for entry in commentary_log[-100:]:
        html += f"<li>{entry}</li>"
    html += "</ul><script>setTimeout(()=>location.reload(), 5000);</script>"
    return html

def run_health_server():
    port = int(os.environ.get('PORT', 10000))
    app.run(host="0.0.0.0", port=port)

# === WADE FUNCTIONS ===

def is_giants_game_today(schedule):
    today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    for game in schedule:
        game_date = game.get("start_time_utc", "")[:10]
        if game_date == today:
            return True
    return False

def get_most_recent_giants_game():
    today = datetime.datetime.now(datetime.UTC)
    seven_days_ago = today - datetime.timedelta(days=7)

    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId=137&startDate={seven_days_ago.strftime('%Y-%m-%d')}&endDate={today.strftime('%Y-%m-%d')}"
    response = requests.get(url).json()
    games = []

    for day in response.get("dates", []):
        for game in day.get("games", []):
            if game["status"]["abstractGameState"] == "Final":
                games.append((game["gameDate"], game["gamePk"]))

    if not games:
        print("❌ No final Giants games found in past 7 days.")
        return None

    games.sort(reverse=True)
    most_recent_game = games[0]
    print(f"🧪 Found most recent completed Giants game on {most_recent_game[0]} with gamePk: {most_recent_game[1]}")
    return most_recent_game[1]

def get_game_id():
    if TEST_MODE:
        game_id = get_most_recent_giants_game()
        if game_id:
            print(f"🧪 [TEST MODE] Using most recent completed Giants Game ID: {game_id}")
        return game_id
    else:
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

def post_to_bluesky_or_log(post_text):
    global client_bsky
    if TEST_MODE:
        print(f"🧪 [TEST POST] {post_text}")
        commentary_log.append(f"🧪 {post_text}")
        with open("test_output.txt", "a", encoding="utf-8") as f:
            f.write(post_text + "\n\n")
    else:
        if client_bsky is None:
            client_bsky = Client()
            client_bsky.login(BLUESKY_HANDLE, BLUESKY_PASSWORD)
        client_bsky.send_post(text=post_text)

# === MAIN ===

try:
    threading.Thread(target=run_health_server, daemon=True).start()
    print("🤖 Wade Live 2.3 (Test Mode + Health Check + Monitor) initialised...")

    while True:
        try:
            print("🕒 Checking if Giants game today...")

            if not TEST_MODE and not is_giants_game_today(giants_schedule):
                print("📆 No Giants game today. Sleeping...")
                time.sleep(SLEEP_INTERVAL)
                continue

            print("✅ Giants game scheduled. Finding Game ID...")

            game_id = get_game_id()
            if not game_id:
                print("❌ No Giants game found. Sleeping...")
                time.sleep(SLEEP_INTERVAL)
                continue

            print(f"📺 Monitoring Game ID: {game_id}")

            plays = fetch_all_plays(game_id)
            print(f"🧪 Fetched {len(plays)} plays from game.")

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

                log_line = f"[{inning}{half}] {batter} — {event.upper()} — Reason: {reason}"
                print(f"📃 {log_line}")
                commentary_log.append(log_line)

                if decision:
                    print("📢 Trigger matched. Generating post...")
                    post = generate_post(desc)
                    post_to_bluesky_or_log(post)

                if TEST_MODE:
                    time.sleep(TEST_PLAY_DELAY)

            if TEST_MODE:
                print("🧪 Test Mode: Finished replaying game. Sleeping indefinitely.")
                while True:
                    time.sleep(60)

            if not TEST_MODE:
                time.sleep(SLEEP_INTERVAL)

        except Exception as e:
            print(f"❌ ERROR during play monitoring: {e}")
            time.sleep(SLEEP_INTERVAL)

except Exception as e:
    print(f"❌ FATAL ERROR at startup: {e}")
