"""
regenerate_readings.py
-------------------------------------------------------
Regenerates ONLY the utility reading history (electricity, water, wifi,
occupancy, hourly telemetry) for every room that already exists in your
database - using whatever logic is currently in data_generator.py.

Run this any time you've changed data_generator.py and want your existing
rooms' history to actually reflect the new logic - simply editing the
generator file does NOT change data that was already written to your
database earlier.

This does NOT touch:
  - hostels
  - rooms
  - users / logins
  - alerts, maintenance tickets, notifications

Usage (from the project root, same folder as data_generator.py):
    python regenerate_readings.py
"""

import database as db
import data_generator as gen

READING_TABLES = [
    "electricity_readings",
    "water_readings",
    "wifi_usage",
    "occupancy_log",
    "hourly_readings",
]


def main():
    rooms = db.get_rooms()
    if not rooms:
        print("No rooms found in the database - nothing to regenerate.")
        return

    print(f"Found {len(rooms)} room(s) across your existing hostels.")
    print("This will DELETE and regenerate their reading history "
          f"({', '.join(READING_TABLES)}) using the current data_generator.py logic.")
    print("Hostels, rooms, logins, alerts, and maintenance tickets are left untouched.")
    confirm = input("Continue? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    conn = db.get_connection()
    for table in READING_TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()
    print("Cleared old readings.")

    gen.generate_readings_for_rooms(rooms, num_days=gen.HOSTEL_NUM_DAYS)
    print(f"Regenerated {gen.HOSTEL_NUM_DAYS} days of readings for all {len(rooms)} room(s).")
    print("Done. Restart your backend server so it picks up the fresh data.")


if __name__ == "__main__":
    main()