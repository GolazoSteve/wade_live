"""
Wade Live 2.5 – Robust play ID fallback
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

SLEEP_INTERVAL = 60
TEAM_ID = 137  # San Francisco Giants

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE")
BLUESKY_PASSWORD = os.getenv("BLUESKY_PASSWORD")

client_ai = OpenAI(api_key=OPENAI_API_KEY)
client_bsky = Client()
client_bsky.login(BLUESKY_HANDLE, BLUESKY_PASSWORD)

with open("wade_prompt.txt", "r", encoding="utf-8") as f:
    WADE_PROMPT = f.read()

try:
    with open("giants_schedule.json", "r", encoding="utf-8") as f:
        giants_schedule = json.load(f)
except Exception as e:
    print(f"❌ Could not load Giants schedule: {e}")
    giants_schedule = []

processed_play_ids = set()
current_game_id = None
latest_play_count = 0
posts_made = 0
log_lines = []

priority_players = {
    "Jung Hoo Lee": {"hits": True, "walks": True, "steals": True},
    "Matt Chapman": {"xbh": True},
    "Tyler Fitzgerald": {"hits": True},
    "Willy Adames": {"xbh": True}
}

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
def log_view():
    html = "<html><body><h1>Wade Debug Log</h1><pre>{}</pre></body></html>".format("\n".join(log_lines[-200:]))
    return html

def wade_loop():
    global current_game_id, latest_play_count, posts_made

    log_lines.append("🤖 Wade Live 2.5 (ID fallback) started...")

    while True:
        try:
            if not
