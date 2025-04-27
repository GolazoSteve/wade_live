# === MAIN ===

try:
    threading.Thread(target=run_health_server, daemon=True).start()
    print("🤖 Wade Live 2.5 (Fixed Test Mode + Always Replay Latest Giants Game) initialised...")

    while True:
        try:
            print("🕒 Starting main loop...")

            if not TEST_MODE:
                print("🕒 Checking if Giants game today...")
                if not is_giants_game_today(giants_schedule):
                    print("📆 No Giants game today. Sleeping...")
                    time.sleep(SLEEP_INTERVAL)
                    continue
                print("✅ Giants game scheduled. Finding Game ID...")
                game_id = get_game_id()
            else:
                print("🧪 TEST MODE active. Fetching most recent completed Giants game...")
                game_id = get_most_recent_giants_game()

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
                desc = play.get("result", {}).get("description", "")
                inning = play.get("about", {}).get("inning", "?")
                half = "T" if play.get("about", {}).get("halfInning") == "top" else "B"

                decision, reason = should_post(play)

                log_line = f"[{inning}{half}] {batter} — {event.upper()} — Decision: {'POST' if decision else 'Skipped'} ({reason})"
                print(f"📃 {log_line}")
                commentary_log.append(log_line)

                if decision:
                    print("📢 Trigger matched. Generating post...")
                    post = generate_post(desc)
                    post_to_bluesky_or_log(post)

                if TEST_MODE:
                    time.sleep(TEST_PLAY_DELAY)

            if TEST_MODE:
                print("🧪 Test Mode: Finished replaying game. Restarting replay...")
                processed_play_ids.clear()
                time.sleep(2)  # Small pause, then replay again

            if not TEST_MODE:
                time.sleep(SLEEP_INTERVAL)

        except Exception as e:
            print(f"❌ ERROR during play monitoring: {e}")
            time.sleep(SLEEP_INTERVAL)

except Exception as e:
    print(f"❌ FATAL ERROR at startup: {e}")
