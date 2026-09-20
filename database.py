"""
database.py
-------------------------------------------------------
Shared database layer for the Hostel Utility Dashboard.
Uses SQLite so the whole project runs with zero external
DB setup (can be swapped for MySQL later just by changing
the connection function).

Tables:
    users                -> login accounts (admin / staff)
    rooms               -> room master data
    electricity_readings-> daily electricity usage per room
    water_readings       -> daily water usage per room
    wifi_usage            -> daily wifi data usage + device count per room
    occupancy_log         -> daily occupancy per room
    alerts               -> anomaly / threshold alerts raised by the AI module
-------------------------------------------------------
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "hostel_data.db")


def get_connection():
    """Return a new SQLite connection with row access by column name."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create all tables if they do not already exist."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'staff',   -- 'admin' | 'staff' | 'warden'
            email TEXT UNIQUE,
            college_name TEXT,
            hostel_id INTEGER,                    -- set only for 'warden' accounts
            floor INTEGER,                         -- set only for 'warden' accounts
            status TEXT NOT NULL DEFAULT 'ACTIVE', -- 'ACTIVE' | 'PENDING'
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # A college has many hostels (Boys Hostel Ruby, Girls Hostel Ganga, ...).
    # Scoped by college_name (matches users.college_name) rather than a
    # separate colleges table, since this project is single-college but the
    # column keeps the door open for multi-college later.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hostels (
            hostel_id INTEGER PRIMARY KEY AUTOINCREMENT,
            college_name TEXT NOT NULL,
            name TEXT NOT NULL,
            hostel_type TEXT NOT NULL DEFAULT 'co-ed',  -- 'boys' | 'girls' | 'co-ed'
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(college_name, name)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS rooms (
            room_id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_no TEXT NOT NULL,
            floor INTEGER NOT NULL,
            capacity INTEGER NOT NULL,
            hostel_id INTEGER,
            FOREIGN KEY (hostel_id) REFERENCES hostels(hostel_id),
            UNIQUE(hostel_id, room_no)
        )
    """)

    # ---- Lightweight migration for DBs created before hostels existed ----
    # (adds the new columns to pre-existing users/rooms tables if missing;
    # no-op on a fresh database since the CREATE TABLE above already has them)
    existing_user_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "email" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    if "college_name" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN college_name TEXT")
    if "hostel_id" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN hostel_id INTEGER")
    if "floor" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN floor INTEGER")
    if "status" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'ACTIVE'")

    existing_room_cols = {r["name"] for r in conn.execute("PRAGMA table_info(rooms)").fetchall()}
    if "hostel_id" not in existing_room_cols:
        conn.execute("ALTER TABLE rooms ADD COLUMN hostel_id INTEGER")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS electricity_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            reading_date TEXT NOT NULL,
            units_kwh REAL NOT NULL,
            FOREIGN KEY (room_id) REFERENCES rooms(room_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS water_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            reading_date TEXT NOT NULL,
            liters REAL NOT NULL,
            FOREIGN KEY (room_id) REFERENCES rooms(room_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS wifi_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            reading_date TEXT NOT NULL,
            data_gb REAL NOT NULL,
            connected_devices INTEGER NOT NULL,
            FOREIGN KEY (room_id) REFERENCES rooms(room_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS occupancy_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            reading_date TEXT NOT NULL,
            occupied_count INTEGER NOT NULL,
            FOREIGN KEY (room_id) REFERENCES rooms(room_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resource_type TEXT NOT NULL,      -- 'electricity', 'water', or 'wifi'
            room_id INTEGER NOT NULL,
            reading_date TEXT NOT NULL,
            message TEXT NOT NULL,
            severity TEXT NOT NULL,           -- 'low' | 'medium' | 'high' | 'critical'
            possible_causes TEXT,
            recommended_action TEXT,
            status TEXT DEFAULT 'ACTIVE',     -- 'ACTIVE' | 'ACKNOWLEDGED' | 'RESOLVED'
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (room_id) REFERENCES rooms(room_id)
        )
    """)

    # Lightweight migration for alerts table
    existing_alert_cols = {r["name"] for r in conn.execute("PRAGMA table_info(alerts)").fetchall()}
    if "possible_causes" not in existing_alert_cols:
        conn.execute("ALTER TABLE alerts ADD COLUMN possible_causes TEXT")
    if "recommended_action" not in existing_alert_cols:
        conn.execute("ALTER TABLE alerts ADD COLUMN recommended_action TEXT")
    if "status" not in existing_alert_cols:
        conn.execute("ALTER TABLE alerts ADD COLUMN status TEXT DEFAULT 'ACTIVE'")

    # Utility rates configuration per college (configurable by admin)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS utility_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            college_name TEXT UNIQUE NOT NULL,
            electricity_rate REAL NOT NULL DEFAULT 8.50,  -- ₹ per kWh
            water_rate REAL NOT NULL DEFAULT 0.05,        -- ₹ per Liter (₹50 / 1000L)
            wifi_rate REAL NOT NULL DEFAULT 15.00,       -- ₹ per GB
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Closed-loop maintenance issue workflow
    cur.execute("""
        CREATE TABLE IF NOT EXISTS maintenance_issues (
            issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
            hostel_id INTEGER NOT NULL,
            room_id INTEGER NOT NULL,
            resource_type TEXT NOT NULL,
            problem TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'medium',      -- 'low' | 'medium' | 'high' | 'critical'
            description TEXT,
            assigned_to INTEGER,                          -- references users.user_id
            status TEXT NOT NULL DEFAULT 'OPEN',          -- 'OPEN' | 'IN_PROGRESS' | 'RESOLVED' | 'CLOSED'
            resolution_notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT,
            FOREIGN KEY (hostel_id) REFERENCES hostels(hostel_id),
            FOREIGN KEY (room_id) REFERENCES rooms(room_id),
            FOREIGN KEY (assigned_to) REFERENCES users(user_id)
        )
    """)

    # In-app notifications
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,                              -- NULL for broadcast, or specific user
            college_name TEXT NOT NULL,
            hostel_id INTEGER,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'alert',           -- 'alert' | 'maintenance' | 'system' | 'recommendation'
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # AI Optimization & Savings Recommendations
    cur.execute("""
        CREATE TABLE IF NOT EXISTS recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hostel_id INTEGER NOT NULL,
            room_id INTEGER,
            resource_type TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            action_items TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'MEDIUM',      -- 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'
            potential_savings_monthly REAL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'ACTIVE',        -- 'ACTIVE' | 'IMPLEMENTED' | 'DISMISSED'
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (hostel_id) REFERENCES hostels(hostel_id)
        )
    """)

    # Real-Time 24-Hour Diurnal Hourly Readings (Academic Schedule Aligned)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hourly_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            reading_date TEXT NOT NULL,
            hour INTEGER NOT NULL,                       -- 0 to 23
            units_kwh REAL NOT NULL,
            liters REAL NOT NULL,
            data_gb REAL NOT NULL,
            connected_devices INTEGER NOT NULL,
            session_type TEXT NOT NULL,                   -- 'SLEEP', 'MORNING_RUSH', 'COLLEGE_CLASS', 'LUNCH', 'COLLEGE_LAB', 'EVENING_RETURN', 'NIGHT_STUDY', 'WEEKEND_DAY'
            is_class_hour INTEGER NOT NULL DEFAULT 0,    -- 1 if during academic college session (room expected empty)
            FOREIGN KEY (room_id) REFERENCES rooms(room_id)
        )
    """)

    conn.commit()
    conn.close()


# ---------------------------------------------------------------
# Generic helper read/write functions used by both backend & AI
# ---------------------------------------------------------------

def fetch_all(query, params=()):
    conn = get_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def fetch_one(query, params=()):
    conn = get_connection()
    row = conn.execute(query, params).fetchone()
    conn.close()
    return dict(row) if row else None


def execute(query, params=()):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(query, params)
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id


def get_rooms(hostel_id=None):
    if hostel_id:
        return fetch_all("SELECT * FROM rooms WHERE hostel_id = ? ORDER BY room_no", (hostel_id,))
    return fetch_all("SELECT * FROM rooms ORDER BY room_no")


def get_user_by_username(username):
    return fetch_one("SELECT * FROM users WHERE username = ?", (username,))


def get_user_by_email(email):
    return fetch_one("SELECT * FROM users WHERE email = ?", (email,))


def get_user_by_id(user_id):
    return fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))


def get_user_by_identifier(identifier):
    """Look a user up by username OR email - lets the login form accept either."""
    return fetch_one(
        "SELECT * FROM users WHERE username = ? OR email = ?", (identifier, identifier)
    )


def get_all_users():
    return fetch_all("SELECT user_id, username, role, email, college_name, hostel_id, created_at FROM users ORDER BY user_id")


def get_users_for_college(college_name):
    # Only ACTIVE users belong in the main accounts table - a warden's
    # self-signup request stays PENDING (and only visible in the Pending
    # Wardens approval queue) until an admin actually approves it.
    return fetch_all(
        "SELECT user_id, username, role, email, college_name, hostel_id, floor, created_at "
        "FROM users WHERE college_name = ? AND status = 'ACTIVE' ORDER BY user_id",
        (college_name,),
    )


def insert_user(username, password_hash, role="staff", email=None, college_name=None, hostel_id=None, floor=None, status="ACTIVE"):
    return execute(
        "INSERT INTO users (username, password_hash, role, email, college_name, hostel_id, floor, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (username, password_hash, role, email, college_name, hostel_id, floor, status),
    )


def get_pending_wardens(college_name):
    return fetch_all(
        "SELECT u.user_id, u.username, u.email, u.hostel_id, u.floor, u.created_at, h.name AS hostel_name "
        "FROM users u LEFT JOIN hostels h ON h.hostel_id = u.hostel_id "
        "WHERE u.college_name = ? AND u.role = 'warden' AND u.status = 'PENDING' "
        "ORDER BY u.created_at",
        (college_name,),
    )


def approve_warden(user_id):
    return execute("UPDATE users SET status = 'ACTIVE' WHERE user_id = ?", (user_id,))


def reject_warden(user_id):
    return execute("DELETE FROM users WHERE user_id = ? AND role = 'warden' AND status = 'PENDING'", (user_id,))


def update_user_password(user_id, password_hash):
    return execute(
        "UPDATE users SET password_hash = ? WHERE user_id = ?",
        (password_hash, user_id),
    )


# ---------------------------------------------------------------
# Hostels
# ---------------------------------------------------------------

def get_hostels(college_name):
    return fetch_all(
        "SELECT * FROM hostels WHERE college_name = ? ORDER BY name", (college_name,)
    )


def get_hostels_public(college_name):
    """Case-insensitive lookup used on the signup screen, before the visitor
    has any session - only exposes name/type, never anything sensitive."""
    return fetch_all(
        "SELECT hostel_id, name, hostel_type FROM hostels WHERE LOWER(TRIM(college_name)) = LOWER(TRIM(?)) ORDER BY name",
        (college_name,),
    )


def get_floors_for_hostel(hostel_id):
    rows = fetch_all(
        "SELECT DISTINCT floor FROM rooms WHERE hostel_id = ? ORDER BY floor", (hostel_id,)
    )
    return [r["floor"] for r in rows]


def get_hostel(hostel_id):
    return fetch_one("SELECT * FROM hostels WHERE hostel_id = ?", (hostel_id,))


def insert_hostel(college_name, name, hostel_type="co-ed"):
    return execute(
        "INSERT INTO hostels (college_name, name, hostel_type) VALUES (?, ?, ?)",
        (college_name, name, hostel_type),
    )


def delete_hostel(hostel_id):
    """Cascade-deletes the hostel's rooms and everything that references
    them (readings, alerts, hourly readings, maintenance issues,
    recommendations, notifications) so the final DELETEs never trip the
    FOREIGN KEY constraint."""
    conn = get_connection()
    room_ids = [r["room_id"] for r in conn.execute(
        "SELECT room_id FROM rooms WHERE hostel_id = ?", (hostel_id,)
    ).fetchall()]
    for room_id in room_ids:
        conn.execute("DELETE FROM electricity_readings WHERE room_id = ?", (room_id,))
        conn.execute("DELETE FROM water_readings WHERE room_id = ?", (room_id,))
        conn.execute("DELETE FROM wifi_usage WHERE room_id = ?", (room_id,))
        conn.execute("DELETE FROM occupancy_log WHERE room_id = ?", (room_id,))
        conn.execute("DELETE FROM alerts WHERE room_id = ?", (room_id,))
        conn.execute("DELETE FROM hourly_readings WHERE room_id = ?", (room_id,))
        conn.execute("DELETE FROM maintenance_issues WHERE room_id = ?", (room_id,))
        conn.execute("DELETE FROM recommendations WHERE room_id = ?", (room_id,))
    conn.execute("DELETE FROM maintenance_issues WHERE hostel_id = ?", (hostel_id,))
    conn.execute("DELETE FROM recommendations WHERE hostel_id = ?", (hostel_id,))
    conn.execute("DELETE FROM notifications WHERE hostel_id = ?", (hostel_id,))
    conn.execute("DELETE FROM rooms WHERE hostel_id = ?", (hostel_id,))
    conn.execute("DELETE FROM hostels WHERE hostel_id = ?", (hostel_id,))
    conn.commit()
    conn.close()


def delete_user(user_id):
    """Removes a users row, clearing any references to it elsewhere first
    so the delete never fails on a stray foreign key."""
    conn = get_connection()
    conn.execute("UPDATE maintenance_issues SET assigned_to = NULL WHERE assigned_to = ?", (user_id,))
    conn.execute("DELETE FROM notifications WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_electricity(room_id=None, limit_days=None):
    q = "SELECT * FROM electricity_readings"
    params = ()
    if room_id:
        q += " WHERE room_id = ?"
        params = (room_id,)
    q += " ORDER BY reading_date"
    rows = fetch_all(q, params)
    if limit_days:
        rows = rows[-limit_days:]
    return rows


def get_water(room_id=None, limit_days=None):
    q = "SELECT * FROM water_readings"
    params = ()
    if room_id:
        q += " WHERE room_id = ?"
        params = (room_id,)
    q += " ORDER BY reading_date"
    rows = fetch_all(q, params)
    if limit_days:
        rows = rows[-limit_days:]
    return rows


def get_wifi(room_id=None, limit_days=None):
    q = "SELECT * FROM wifi_usage"
    params = ()
    if room_id:
        q += " WHERE room_id = ?"
        params = (room_id,)
    q += " ORDER BY reading_date"
    rows = fetch_all(q, params)
    if limit_days:
        rows = rows[-limit_days:]
    return rows


def get_occupancy(room_id=None, limit_days=None):
    q = "SELECT * FROM occupancy_log"
    params = ()
    if room_id:
        q += " WHERE room_id = ?"
        params = (room_id,)
    q += " ORDER BY reading_date"
    rows = fetch_all(q, params)
    if limit_days:
        rows = rows[-limit_days:]
    return rows


def room_ids_for_scope(hostel_id=None, floor=None):
    """Room ids for a hostel, optionally narrowed to one floor within it -
    the shared building block behind floor-/hostel-level aggregated totals."""
    rooms = get_rooms(hostel_id=hostel_id) if hostel_id else get_rooms()
    if floor is not None:
        rooms = [r for r in rooms if r.get("floor") == floor]
    return [r["room_id"] for r in rooms]


def _aggregate_by_date(table, value_cols, room_ids, limit_days=None):
    """Sums one or more numeric columns from `table` across `room_ids`,
    grouped by reading_date - the shared logic behind every *_aggregated()
    function below (floor-/hostel-level daily totals instead of one room)."""
    if not room_ids:
        return []
    placeholders = ",".join("?" for _ in room_ids)
    sums = ", ".join(f"SUM({c}) AS {c}" for c in value_cols)
    q = (
        f"SELECT reading_date, {sums} FROM {table} "
        f"WHERE room_id IN ({placeholders}) GROUP BY reading_date ORDER BY reading_date"
    )
    rows = fetch_all(q, tuple(room_ids))
    if limit_days:
        rows = rows[-limit_days:]
    return rows


def get_electricity_aggregated(room_ids, limit_days=None):
    return _aggregate_by_date("electricity_readings", ["units_kwh"], room_ids, limit_days)


def get_water_aggregated(room_ids, limit_days=None):
    return _aggregate_by_date("water_readings", ["liters"], room_ids, limit_days)


def get_wifi_aggregated(room_ids, limit_days=None):
    return _aggregate_by_date("wifi_usage", ["data_gb", "connected_devices"], room_ids, limit_days)


def get_occupancy_aggregated(room_ids, limit_days=None):
    return _aggregate_by_date("occupancy_log", ["occupied_count"], room_ids, limit_days)


def get_typical_peak_hour(resource_type, dow, room_ids):
    """The hour (0-23) with historically the highest average usage on the
    given day-of-week (Python convention: 0=Monday..6=Sunday) across
    room_ids - used to label a forecast day's "usually peaks around" hour,
    since a future date has no real hourly reading of its own yet."""
    if not room_ids:
        return None
    value_col = {"electricity": "units_kwh", "water": "liters", "wifi": "data_gb"}.get(resource_type)
    if not value_col:
        return None
    sqlite_dow = (dow + 1) % 7  # Python Mon=0..Sun=6 -> SQLite strftime('%w') Sun=0..Sat=6
    placeholders = ",".join("?" for _ in room_ids)
    row = fetch_one(
        f"SELECT hour FROM hourly_readings WHERE room_id IN ({placeholders}) "
        f"AND CAST(strftime('%w', reading_date) AS INTEGER) = ? "
        f"GROUP BY hour ORDER BY AVG({value_col}) DESC LIMIT 1",
        (*room_ids, sqlite_dow),
    )
    return row["hour"] if row else None


def get_peak_hour(resource_type, reading_date, room_ids):
    """The hour (0-23) with the highest total resource usage across
    room_ids on reading_date, or None if that date falls outside the
    hourly_readings retention window (no hourly data was ever stored for it)."""
    if not room_ids:
        return None
    value_col = {"electricity": "units_kwh", "water": "liters", "wifi": "data_gb"}.get(resource_type)
    if not value_col:
        return None
    placeholders = ",".join("?" for _ in room_ids)
    row = fetch_one(
        f"SELECT hour FROM hourly_readings WHERE room_id IN ({placeholders}) AND reading_date = ? "
        f"GROUP BY hour ORDER BY SUM({value_col}) DESC LIMIT 1",
        (*room_ids, reading_date),
    )
    return row["hour"] if row else None


def get_alerts(resource_type=None, limit=50, hostel_id=None, severity=None, status=None):
    q = """SELECT alerts.*, rooms.room_no, rooms.floor, rooms.hostel_id, hostels.name as hostel_name 
           FROM alerts 
           JOIN rooms ON alerts.room_id = rooms.room_id
           JOIN hostels ON rooms.hostel_id = hostels.hostel_id
           WHERE 1=1"""
    params = []
    if resource_type:
        q += " AND alerts.resource_type = ?"
        params.append(resource_type)
    if hostel_id:
        q += " AND rooms.hostel_id = ?"
        params.append(hostel_id)
    if severity:
        q += " AND alerts.severity = ?"
        params.append(severity)
    if status:
        q += " AND alerts.status = ?"
        params.append(status)
    q += " ORDER BY alerts.created_at DESC LIMIT ?"
    params.append(limit)
    return fetch_all(q, tuple(params))


def insert_alert(resource_type, room_id, reading_date, message, severity):
    return execute(
        """INSERT INTO alerts (resource_type, room_id, reading_date, message, severity)
           VALUES (?, ?, ?, ?, ?)""",
        (resource_type, room_id, reading_date, message, severity),
    )


def insert_alert_extended(resource_type, room_id, reading_date, message, severity, possible_causes="", recommended_action="", status="ACTIVE"):
    return execute(
        """INSERT INTO alerts (resource_type, room_id, reading_date, message, severity, possible_causes, recommended_action, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (resource_type, room_id, reading_date, message, severity, possible_causes, recommended_action, status),
    )


def update_alert_status(alert_id, status):
    return execute("UPDATE alerts SET status = ? WHERE id = ?", (status, alert_id))


# ---------------------------------------------------------------
# Utility Rates
# ---------------------------------------------------------------

def get_utility_rates(college_name):
    if not college_name:
        return {"electricity_rate": 8.50, "water_rate": 0.05, "wifi_rate": 15.00}
    row = fetch_one("SELECT * FROM utility_rates WHERE college_name = ?", (college_name,))
    if not row:
        # Insert defaults for this college
        execute(
            "INSERT OR IGNORE INTO utility_rates (college_name, electricity_rate, water_rate, wifi_rate) VALUES (?, 8.50, 0.05, 15.00)",
            (college_name,),
        )
        return {"electricity_rate": 8.50, "water_rate": 0.05, "wifi_rate": 15.00}
    return dict(row)


def set_utility_rates(college_name, electricity_rate, water_rate, wifi_rate):
    execute("""
        INSERT INTO utility_rates (college_name, electricity_rate, water_rate, wifi_rate, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(college_name) DO UPDATE SET
            electricity_rate=excluded.electricity_rate,
            water_rate=excluded.water_rate,
            wifi_rate=excluded.wifi_rate,
            updated_at=CURRENT_TIMESTAMP
    """, (college_name, float(electricity_rate), float(water_rate), float(wifi_rate)))
    return get_utility_rates(college_name)


# ---------------------------------------------------------------
# Maintenance Issues Workflow
# ---------------------------------------------------------------

def get_maintenance_issues(hostel_id=None, assigned_to=None, status=None, issue_id=None):
    q = """
        SELECT m.*, r.room_no, r.floor, h.name as hostel_name, u.username as assigned_username
        FROM maintenance_issues m
        JOIN rooms r ON m.room_id = r.room_id
        JOIN hostels h ON m.hostel_id = h.hostel_id
        LEFT JOIN users u ON m.assigned_to = u.user_id
        WHERE 1=1
    """
    params = []
    if issue_id:
        q += " AND m.issue_id = ?"
        params.append(issue_id)
        return fetch_one(q, tuple(params))
    if hostel_id:
        q += " AND m.hostel_id = ?"
        params.append(hostel_id)
    if assigned_to:
        q += " AND m.assigned_to = ?"
        params.append(assigned_to)
    if status:
        q += " AND m.status = ?"
        params.append(status)
    q += " ORDER BY CASE m.severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, m.created_at DESC"
    return fetch_all(q, tuple(params))


def insert_maintenance_issue(hostel_id, room_id, resource_type, problem, severity="medium", description="", assigned_to=None):
    return execute(
        """INSERT INTO maintenance_issues (hostel_id, room_id, resource_type, problem, severity, description, assigned_to, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN')""",
        (hostel_id, room_id, resource_type, problem, severity.lower(), description, assigned_to),
    )


def update_maintenance_issue(issue_id, status=None, resolution_notes=None, assigned_to=None):
    updates = []
    params = []
    if status:
        updates.append("status = ?")
        params.append(status.upper())
        if status.upper() in ("RESOLVED", "CLOSED"):
            updates.append("resolved_at = CURRENT_TIMESTAMP")
    if resolution_notes is not None:
        updates.append("resolution_notes = ?")
        params.append(resolution_notes)
    if assigned_to is not None:
        updates.append("assigned_to = ?")
        params.append(assigned_to)

    if not updates:
        return
    params.append(issue_id)
    execute(f"UPDATE maintenance_issues SET {', '.join(updates)} WHERE issue_id = ?", tuple(params))
    return get_maintenance_issues(issue_id=issue_id)


# ---------------------------------------------------------------
# In-App Notifications
# ---------------------------------------------------------------

def get_notifications(college_name=None, user_id=None, unread_only=False, limit=25):
    q = "SELECT * FROM notifications WHERE 1=1"
    params = []
    if college_name:
        q += " AND college_name = ?"
        params.append(college_name)
    if user_id:
        q += " AND (user_id IS NULL OR user_id = ?)"
        params.append(user_id)
    if unread_only:
        q += " AND is_read = 0"
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return fetch_all(q, tuple(params))


def insert_notification(college_name, title, message, user_id=None, hostel_id=None, type="alert"):
    return execute(
        """INSERT INTO notifications (user_id, college_name, hostel_id, title, message, type, is_read)
           VALUES (?, ?, ?, ?, ?, ?, 0)""",
        (user_id, college_name, hostel_id, title, message, type),
    )


def mark_notifications_read(college_name, user_id=None, notification_id=None):
    if notification_id:
        execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (notification_id,))
    elif user_id:
        execute("UPDATE notifications SET is_read = 1 WHERE college_name = ? AND (user_id IS NULL OR user_id = ?)", (college_name, user_id))
    else:
        execute("UPDATE notifications SET is_read = 1 WHERE college_name = ?", (college_name,))


# ---------------------------------------------------------------
# AI Recommendations & Savings
# ---------------------------------------------------------------

def get_recommendations(hostel_id=None, status="ACTIVE"):
    q = """
        SELECT rec.*, h.name as hostel_name, r.room_no 
        FROM recommendations rec
        JOIN hostels h ON rec.hostel_id = h.hostel_id
        LEFT JOIN rooms r ON rec.room_id = r.room_id
        WHERE 1=1
    """
    params = []
    if hostel_id:
        q += " AND rec.hostel_id = ?"
        params.append(hostel_id)
    if status:
        q += " AND rec.status = ?"
        params.append(status)
    q += " ORDER BY CASE rec.priority WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 ELSE 4 END, rec.created_at DESC"
    return fetch_all(q, tuple(params))


def insert_recommendation(hostel_id, room_id, resource_type, title, description, action_items, priority="MEDIUM", potential_savings_monthly=0.0):
    return execute(
        """INSERT INTO recommendations (hostel_id, room_id, resource_type, title, description, action_items, priority, potential_savings_monthly, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')""",
        (hostel_id, room_id, resource_type, title, description, action_items, priority.upper(), float(potential_savings_monthly)),
    )


def update_recommendation_status(rec_id, status):
    return execute("UPDATE recommendations SET status = ? WHERE id = ?", (status.upper(), rec_id))


# ---------------------------------------------------------------
# Real-Time 24-Hour Diurnal Hourly Readings
# ---------------------------------------------------------------

def get_hourly_readings(room_id, date_str=None):
    """Returns the 24-hour readings for a given room and date.
    If date_str is None, returns the most recent date available."""
    if not date_str:
        latest = fetch_one("SELECT MAX(reading_date) as max_d FROM hourly_readings WHERE room_id = ?", (room_id,))
        date_str = latest["max_d"] if latest and latest.get("max_d") else None
    if not date_str:
        # Fallback to today
        from datetime import date
        date_str = date.today().strftime("%Y-%m-%d")

    rows = fetch_all(
        "SELECT * FROM hourly_readings WHERE room_id = ? AND reading_date = ? ORDER BY hour ASC",
        (room_id, date_str),
    )
    return rows


def get_hourly_readings_range(room_id, days=7):
    """Returns real hour-by-hour readings across the most recent `days` days
    of hourly data actually stored for this room (bounded by however far back
    hourly retention goes - currently up to 14 days), ordered chronologically.
    Used for the "1 Week" timing view so usage-by-time-of-day can be seen
    across several real days, not just a single day."""
    latest = fetch_one("SELECT MAX(reading_date) as max_d FROM hourly_readings WHERE room_id = ?", (room_id,))
    if not latest or not latest.get("max_d"):
        return []
    from datetime import datetime as _dt, timedelta
    end_date = _dt.strptime(latest["max_d"], "%Y-%m-%d").date()
    start_date = end_date - timedelta(days=days - 1)
    rows = fetch_all(
        "SELECT * FROM hourly_readings WHERE room_id = ? AND reading_date >= ? AND reading_date <= ? ORDER BY reading_date ASC, hour ASC",
        (room_id, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
    )
    return rows


def get_diurnal_profile(room_id=None, hostel_id=None, day_type="weekday"):
    """
    Computes an average 24-hour diurnal profile across rooms/hostels,
    categorized by academic session types (College Class Hours, Morning Rush, Evening Peak).
    """
    where_clauses = []
    params = []
    if room_id:
        where_clauses.append("hr.room_id = ?")
        params.append(room_id)
    elif hostel_id:
        where_clauses.append("r.hostel_id = ?")
        params.append(hostel_id)

    # Filter weekday vs weekend if records exist
    if day_type == "weekday":
        where_clauses.append("hr.session_type != 'WEEKEND_DAY'")
    elif day_type == "weekend":
        where_clauses.append("hr.session_type = 'WEEKEND_DAY'")

    where_str = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    q = f"""
        SELECT 
            hr.hour,
            hr.session_type,
            MAX(hr.is_class_hour) as is_class_hour,
            ROUND(AVG(hr.units_kwh), 3) as avg_kwh,
            ROUND(AVG(hr.liters), 1) as avg_liters,
            ROUND(AVG(hr.data_gb), 3) as avg_wifi_gb,
            ROUND(AVG(hr.connected_devices), 1) as avg_devices
        FROM hourly_readings hr
        JOIN rooms r ON hr.room_id = r.room_id
        {where_str}
        GROUP BY hr.hour
        ORDER BY hr.hour ASC
    """
    return fetch_all(q, tuple(params))


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")