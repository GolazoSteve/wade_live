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

SLEEP_INTERVAL = 60
TEAM_ID = 137

# Environment
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE")
BLUESKY_PASSWORD = os.getenv("BLUESKY_PASSWORD")

# Clients
client_ai = OpenAI(api_key=OPENAI_API_KEY)
client_bsky = None

# Memory log for monitor
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
    html = "<h1>Wade Live Monitor</h1><ul style='font-family:monospace; font-size:14px;'>"
    for entry in commentary_log[-150:]:
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

def get_game_id():
    today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}"
    response = requests.get(url)
    try:
        data = response.json()
    except Exception as e:
        print(f"❌ Failed to parse JSON: {e}")
        return None

    for date in data.get("dates", []):
        for game in date.get("games", []):
            away_id = game["teams"]["away"]["team"]["id"]
            home_id = game["teams"]["home"]["team"]["id"]
            if TEAM_ID in [away_id, home_id]:
                print(f"✅ Giants game found: {game['gamePk']}")
                return game["gamePk"]

    print("❌ No Giants game found today.")
    return None

def fetch_all_plays(game_id):
    url = f"https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live"
    response = requests.get(url)
    return response.json().get("liveData", {}).get("plays", {}).get("allPlays", [])

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
        return False, "No post: Event not finalized yet"

    if not is_giants_pa(play):
        return False, "No post: Opponent batter"

    if event == "Home Run":
        return True, "POST: Giants Home Run"

    if rbi > 0:
        return True, "POST: Giants RBI play"

    if batter in priority_players:
        if batter == "Jung Hoo Lee":
            if event in {"Single", "Double", "Triple", "Home Run", "Walk", "Hit By Pitch"}:
                return True, f"POST: Jung Hoo Lee {event}"
            if event == "Stolen Base":
                return True, "POST: Jung Hoo Lee Stolen Base"
        elif batter == "Matt Chapman" and event in {"Double", "Triple", "Home Run"}:
            return True, f"POST: Matt Chapman {event}"
        elif batter == "Tyler Fitzgerald" and event in {"Single", "Double", "Triple", "Home Run"}:
            return True, f"POST: Tyler Fitzgerald {event}"
        elif batter == "Willy Adames" and event in {"Double", "Triple", "Home Run"}:
            return True, f"POST: Willy Adames {event}"

    return False, "No post: Routine Giants out"

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
    if client_bsky is None:
        client_bsky = Client()
        client_bsky.login(BLUESKY_HANDLE, BLUESKY_PASSWORD)
    client_bsky.send_post(text=post_text)

# === MAIN ===

try:
    threading.Thread(target=run_health_server, daemon=True).start()
    print("🤖 Wade Live Started...")

    while True:
        try:
            print("🕒 Main loop starting...")

            if not is_giants_game_today(giants_schedule):
                print("🗓️ No Giants game today. Sleeping...")
                time.sleep(SLEEP_INTERVAL)
                continue

            print("✅ Giants game scheduled. Finding Game ID...")
            game_id = get_game_id()

            if not game_id:
                print("❌ No valid Giants game found. Sleeping...")
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
                inning = play.get("about", {}).get("inning", "?")
                half = "T" if play.get("about", {}).get("halfInning") == "top" else "B"

                decision, reason = should_post(play)
                icon = "✅" if decision else "❌"
                log_line = f"[Inning {inning}{half}] Batter: {batter} | Event: {event} | {icon} {reason}"

                print(log_line)
                commentary_log.append(log_line)

                if decision:
                    print("📢 Trigger matched. Generating post...")
                    post = generate_post(play.get("result", {}).get("description", ""))
                    post_to_bluesky_or_log(post)

            time.sleep(SLEEP_INTERVAL)

        except Exception as e:
            print(f"❌ ERROR during play monitoring: {e}")
            time.sleep(SLEEP_INTERVAL)

except Exception as e:
    print(f"❌ FATAL ERROR at startup: {e}")
