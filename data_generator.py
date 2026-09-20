"""
data_generator.py
-------------------------------------------------------
Simulates smart-meter sensor data for the Hostel Utility Dashboard,
since a real IoT deployment isn't available for the demo.

Generates, per room:
  - electricity_readings (daily units_kwh)
  - water_readings        (daily liters)
  - wifi_usage             (daily data_gb + connected_devices)
  - occupancy_log          (daily occupied_count)
  - hourly_readings        (24h telemetry, academic-schedule aligned,
                              kept for the most recent HOURLY_RETENTION_DAYS
                              days only, to keep the DB small)

Patterns are weekday/weekend aware (rooms are mostly empty during class
hours on weekdays, fuller in the evening/night and on weekends), and a
small fraction of readings are intentionally spiked so the AI module's
IsolationForest anomaly detector has real anomalies to catch.

Shared module - owned jointly by the team, used by:
  - setup_project.py  (reset_and_seed, once at first-time setup)
  - auth.py / backend_api.py (seed_demo_hostel, whenever a new hostel
    is created, so its dashboard isn't empty)
-------------------------------------------------------
"""

import random
from datetime import datetime, timedelta

import database as db

# ---------------------------------------------------------------
# Constants
# ---------------------------------------------------------------

HOSTEL_NUM_ROOMS = 12     # rooms seeded for every new hostel
HOSTEL_NUM_DAYS = 120     # days of daily reading history per new hostel
ROOM_CAPACITY = 3         # occupants per room

# legacy aliases (kept for anything referencing the older names)
NUM_ROOMS = HOSTEL_NUM_ROOMS
NUM_DAYS = HOSTEL_NUM_DAYS

HOURLY_RETENTION_DAYS = 14  # hourly telemetry is only kept for recent days

ANOMALY_RATE = 0.015        # ~1.5% of daily readings are spiked

READING_TABLES = [
    "electricity_readings",
    "water_readings",
    "wifi_usage",
    "occupancy_log",
    "hourly_readings",
]


# ---------------------------------------------------------------
# Room seeding
# ---------------------------------------------------------------

def seed_rooms(hostel_id, num_rooms=HOSTEL_NUM_ROOMS):
    """Creates a fixed demo set of rooms for a hostel (4 per floor)."""
    rooms = []
    per_floor = 4
    for i in range(num_rooms):
        floor = (i // per_floor) + 1
        room_no = f"{floor}{str((i % per_floor) + 1).zfill(2)}"
        room_id = db.execute(
            "INSERT INTO rooms (room_no, floor, capacity, hostel_id) VALUES (?, ?, ?, ?)",
            (room_no, floor, ROOM_CAPACITY, hostel_id),
        )
        rooms.append({
            "room_id": room_id,
            "room_no": room_no,
            "floor": floor,
            "capacity": ROOM_CAPACITY,
            "hostel_id": hostel_id,
        })
    return rooms


# ---------------------------------------------------------------
# Daily readings (electricity / water / wifi / occupancy)
# ---------------------------------------------------------------

def _is_weekend(date_obj):
    return date_obj.weekday() >= 5  # 5=Sat, 6=Sun


def _daily_occupancy(capacity, is_weekend):
    """Most residents are present most nights; weekends skew fuller
    (fewer people out at class/labs during the day)."""
    if is_weekend:
        occ = capacity - random.choice([0, 0, 0, 1])
    else:
        occ = capacity - random.choice([0, 0, 1, 1, 2])
    return max(0, min(capacity, occ))


def generate_readings_for_rooms(rooms, num_days=HOSTEL_NUM_DAYS):
    """(Re)generates daily electricity/water/wifi/occupancy history for
    the given rooms, ending today, plus hourly telemetry for the most
    recent HOURLY_RETENTION_DAYS days."""
    today = datetime.now().date()
    start_date = today - timedelta(days=num_days - 1)

    for room in rooms:
        capacity = room["capacity"]
        for day_offset in range(num_days):
            date_obj = start_date + timedelta(days=day_offset)
            date_str = date_obj.strftime("%Y-%m-%d")
            weekend = _is_weekend(date_obj)

            occupied = _daily_occupancy(capacity, weekend)
            occ_ratio = occupied / capacity if capacity else 0

            # --- electricity (kWh/day) ---
            base_kwh = 2.0 + occ_ratio * 4.5
            units_kwh = max(0.1, round(base_kwh + random.gauss(0, 0.5), 2))

            # --- water (liters/day) ---
            base_liters = 30 + occupied * 45
            liters = max(0.0, round(base_liters + random.gauss(0, 15), 1))

            # --- wifi (GB/day + devices) ---
            base_gb = 0.8 + occupied * 1.6 + (0.5 if weekend else 0)
            data_gb = max(0.0, round(base_gb + random.gauss(0, 0.4), 2))
            connected_devices = max(0, occupied * random.choice([1, 1, 2]))

            # --- inject anomalies (leaks, appliances left on, faulty meter) ---
            if random.random() < ANOMALY_RATE:
                units_kwh = round(units_kwh * random.uniform(2.5, 4.0), 2)
            if random.random() < ANOMALY_RATE:
                liters = round(liters * random.uniform(2.5, 5.0), 1)
            if random.random() < ANOMALY_RATE:
                data_gb = round(data_gb * random.uniform(2.0, 3.5), 2)

            db.execute(
                "INSERT INTO electricity_readings (room_id, reading_date, units_kwh) VALUES (?, ?, ?)",
                (room["room_id"], date_str, units_kwh),
            )
            db.execute(
                "INSERT INTO water_readings (room_id, reading_date, liters) VALUES (?, ?, ?)",
                (room["room_id"], date_str, liters),
            )
            db.execute(
                "INSERT INTO wifi_usage (room_id, reading_date, data_gb, connected_devices) VALUES (?, ?, ?, ?)",
                (room["room_id"], date_str, data_gb, connected_devices),
            )
            db.execute(
                "INSERT INTO occupancy_log (room_id, reading_date, occupied_count) VALUES (?, ?, ?)",
                (room["room_id"], date_str, occupied),
            )

        # Hourly telemetry only for the most recent window, to keep the DB small
        hourly_start = today - timedelta(days=HOURLY_RETENTION_DAYS - 1)
        for day_offset in range(HOURLY_RETENTION_DAYS):
            date_obj = hourly_start + timedelta(days=day_offset)
            generate_hourly_telemetry(room, date_obj)


# ---------------------------------------------------------------
# Hourly telemetry (24h diurnal profile, academic-schedule aligned)
# ---------------------------------------------------------------

def _session_for_hour(hour, weekend):
    """Maps an hour-of-day to a named session, aligned to a typical
    college/hostel daily schedule. is_class_hour marks hours the room
    is expected to be empty because residents are in class/labs."""
    if weekend:
        return "WEEKEND_DAY", 0
    if 0 <= hour < 6:
        return "SLEEP", 0
    if 6 <= hour < 8:
        return "MORNING_RUSH", 0
    if 8 <= hour < 12:
        return "COLLEGE_CLASS", 1
    if 12 <= hour < 13:
        return "LUNCH", 0
    if 13 <= hour < 16:
        return "COLLEGE_LAB", 1
    if 16 <= hour < 19:
        return "EVENING_RETURN", 0
    if 19 <= hour < 22:
        return "NIGHT_STUDY", 0
    return "SLEEP", 0


def generate_hourly_telemetry(room, date_obj):
    """Generates and inserts 24 hourly_readings rows for one room/date."""
    capacity = room["capacity"]
    weekend = _is_weekend(date_obj)
    date_str = date_obj.strftime("%Y-%m-%d")

    for hour in range(24):
        session_type, is_class_hour = _session_for_hour(hour, weekend)

        if is_class_hour:
            occ_ratio = random.uniform(0.0, 0.15)
        elif session_type in ("SLEEP", "NIGHT_STUDY"):
            occ_ratio = random.uniform(0.75, 1.0)
        elif session_type == "WEEKEND_DAY":
            occ_ratio = random.uniform(0.5, 0.9)
        else:
            occ_ratio = random.uniform(0.3, 0.7)

        units_kwh = round(max(0.0, 0.05 + occ_ratio * 0.35 + random.gauss(0, 0.03)), 3)
        liters = round(max(0.0, occ_ratio * capacity * 2.5 + random.gauss(0, 1.0)), 1)
        data_gb = round(max(0.0, 0.02 + occ_ratio * 0.12 + random.gauss(0, 0.01)), 3)
        connected_devices = max(0, round(occ_ratio * capacity * random.uniform(0.8, 1.5)))

        db.execute(
            """INSERT INTO hourly_readings
               (room_id, reading_date, hour, units_kwh, liters, data_gb,
                connected_devices, session_type, is_class_hour)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (room["room_id"], date_str, hour, units_kwh, liters, data_gb,
             connected_devices, session_type, is_class_hour),
        )


# ---------------------------------------------------------------
# High-level entry points
# ---------------------------------------------------------------

def seed_demo_hostel(hostel_id):
    """Seeds a brand-new hostel with a working demo room set and full
    reading history, so its dashboard has real data right away.
    Called from backend_api.py whenever an admin creates a hostel."""
    rooms = seed_rooms(hostel_id, HOSTEL_NUM_ROOMS)
    generate_readings_for_rooms(rooms, HOSTEL_NUM_DAYS)
    return rooms


def reset_and_seed():
    """Clears any existing reading/room/hostel data so setup starts from
    a clean slate. Hostels themselves are created later via the signup/
    onboarding flow (see auth.py), not here - this just guarantees a
    fresh DB rather than leftover data from a previous run."""
    conn = db.get_connection()
    for table in READING_TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM rooms")
    conn.commit()
    conn.close()
    print("Database cleared - ready for a fresh hostel to be created via Sign Up.")


if __name__ == "__main__":
    db.init_db()
    reset_and_seed()