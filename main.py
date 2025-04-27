print("📡 Wade Watch LIVE MODE ENGAGED. LET'S GO GIANTS")
game_id = get_game_id(TEAM_ID)
if not game_id:
    print("❌ No Giants game found today.")
    exit()

print(f"🎮 Watching Game ID: {game_id}")

while True:
    try:
        data = fetch_plays(game_id)
        linescore = data.get("liveData", {}).get("linescore", {})
        plays = data.get("liveData", {}).get("plays", {}).get("allPlays", [])

        if not plays:
            print("⏳ No plays yet. Waiting...")
            time.sleep(SLEEP_INTERVAL)
            continue

        latest_play = plays[-1]
        play_id = latest_play.get("playId") or latest_play.get("playEndTime")
        atbat_index = latest_play.get("atBatIndex")
        event = latest_play.get("result", {}).get("event")
        desc = latest_play.get("result", {}).get("description")
        batter = latest_play.get("matchup", {}).get("batter", {}).get("fullName", "Unknown")

        # Only react to completed plays
        if not event or not desc or event.lower() == "pending":
            print(f"⏳ Incomplete play for {batter} — skipping.")
            time.sleep(SLEEP_INTERVAL)
            continue

        team_id_raw = get_team_id_from_play(latest_play)
        team_label = team_map.get(team_id_raw, f"Team ID: {team_id_raw}" if team_id_raw else "Unknown")

        inning = latest_play.get("about", {}).get("inning", "?")
        half = "Top" if latest_play.get("about", {}).get("halfInning") == "top" else "Bottom"

        print(f"\n📡 [{datetime.datetime.now().strftime('%H:%M:%S')}] [{inning} {half}] {batter} — {event} — {desc}")
        print(f"🧠 Wade Brain: evaluating play...")

        if not play_id:
            print("⚠️ Skipped: Missing play ID.")
            time.sleep(SLEEP_INTERVAL)
            continue

        if play_id in posted_play_ids:
            print("🔁 Skipped: Already processed this play.")
            time.sleep(SLEEP_INTERVAL)
            continue

        posted_play_ids.add(play_id)

        if not is_giants_at_bat(latest_play):
            print("📋 Play not by Giants batter. No post needed.")
            time.sleep(SLEEP_INTERVAL)
            continue

        if atbat_index != last_giants_atbat_index:
            last_giants_atbat_index = atbat_index
            if not should_post(latest_play, linescore):
                plate_appearance_drought += 1
                print(f"🛑 No posting trigger detected. Drought count: {plate_appearance_drought}")
            else:
                plate_appearance_drought = 0
        else:
            print("🔁 Same plate appearance. No new decision needed.")
            time.sleep(SLEEP_INTERVAL)
            continue

        if plate_appearance_drought >= EXISTENTIAL_THRESHOLD:
            existential_post = generate_existential_post()
            print(f"🌀 Existential Post Triggered: {existential_post}")
            client_bsky.send_post(text=existential_post)
            plate_appearance_drought = 0
            continue

        if not should_post(latest_play, linescore):
            print("🛑 Did not meet posting criteria.")
            time.sleep(SLEEP_INTERVAL)
            continue

        if not allowed_to_post():
            print("🛑 Skipped: Rate limit reached. Waiting...")
            time.sleep(SLEEP_INTERVAL)
            continue

        print("✅ Posting trigger detected. Generating post...")
        post = generate_post(desc)
        print(f"📤 Posting to Bluesky: {post}")
        client_bsky.send_post(text=post)
        log_post(latest_play, post, game_id)

        plate_appearance_drought = 0

        if os.path.exists("kill_wade.txt"):
            print("🛑 Kill switch triggered. Shutting down...")
            os.remove("kill_wade.txt")
            break

        time.sleep(SLEEP_INTERVAL)

    except Exception as e:
        print(f"❌ ERROR: {e}")
        time.sleep(SLEEP_INTERVAL)
