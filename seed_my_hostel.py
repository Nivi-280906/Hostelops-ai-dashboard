"""
seed_my_hostel.py
-------------------------------------------------------
Populates every hostel that currently has ZERO rooms with:
  - 12 rooms (data_generator.HOSTEL_NUM_ROOMS)
  - 120 days of realistic simulated electricity/water/wifi
    telemetry (data_generator.HOSTEL_NUM_DAYS)
then retrains the forecasting + anomaly-detection models so
Predictions/Alerts/Efficiency also populate.

This is why signing up and creating a hostel through the UI shows
all zeros on the dashboard: the signup form only creates the hostel
record itself - there's no "Add Room" feature yet to generate rooms
or telemetry for it. This script fills that gap for testing/demo
purposes with SIMULATED data, not real sensor readings.

Run:  python seed_my_hostel.py
-------------------------------------------------------
"""

import database as db
import data_generator as gen
import ai_models as ai

db.init_db()
conn = db.get_connection()
hostels = conn.execute("SELECT hostel_id, name, college_name FROM hostels").fetchall()

if not hostels:
    print("No hostels found at all. Sign up / create a hostel first, then re-run this.")
else:
    seeded_any = False
    for h in hostels:
        hostel_id, name, college_name = h["hostel_id"], h["name"], h["college_name"]
        room_count = conn.execute(
            "SELECT COUNT(*) AS c FROM rooms WHERE hostel_id = ?", (hostel_id,)
        ).fetchone()["c"]
        if room_count > 0:
            print(f"Skipping '{name}' ({college_name}) - already has {room_count} rooms.")
            continue
        print(f"Seeding '{name}' ({college_name}) with rooms + 120 days of simulated telemetry...")
        gen.seed_demo_hostel(hostel_id)
        seeded_any = True

    if seeded_any:
        print("Training AI models (forecasting + anomaly detection)...")
        ai.train_all_models()
        ai.detect_anomalies("electricity")
        ai.detect_anomalies("water")
        ai.detect_anomalies("wifi")
        print("Done. Refresh the dashboard - it should now show real numbers.")
    else:
        print("Every hostel already had rooms - nothing to seed.")