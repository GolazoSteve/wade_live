# wade_sandbox.py

"""
Wade Sandbox 1.0
- Simulates a full Giants game from saved JSON
- Applies posting logic and generates posts via OpenAI
- Does not post to Bluesky (prints instead)
"""

import os
import time
import json
import datetime
from openai import OpenAI

# === CONFIGURATION ===

TEAM_ID = 137  # San Francisco Giants
SLEEP_INTERVAL = 0.2  # 5x real-time

# Load environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client_ai = OpenAI(api_key=OPENAI_API_KEY)

# Load Wade's prompt
with open("wade_prompt.txt", "r", encoding="utf-8") as f:
    WADE_PROMPT = f.read()

# Player priorities
priority_players = {
    "Jung Hoo Lee": {"hits": True, "walks": True, "steals": True},
    "Matt Chapman": {"xbh": True},
    "Tyler Fitzgerald": {"hits": True},
    "Willy Adames": {"xbh": True}
}

processed_play_ids = set()

# === FUNCTIONS ===

def load_sample_game():
    with open("sample_game.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["allPlays"]

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
    with open("sandbox_posts_log.txt", "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {post_text}\n")

# === MAIN LOOP ===

def run_sandbox():
    print("🧪 Launching WADE Sandbox...")
    plays = load_sample_game()
    print(f"📅 Loaded {len(plays)} plays from sample_game.json")

    post_count = 0

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
            print(f"\n=== 🟠 WADE SANDBOX POST ===\n{post}\n===========================\n")
            log_post(post)
            post_count += 1

        time.sleep(SLEEP_INTERVAL)

    print(f"✅ Sandbox complete. Total posts generated: {post_count}")

# Run the sandbox
if __name__ == "__main__":
    run_sandbox()
