"""
check_state.py
-------------------------------------------------------
Read-only diagnostic: prints every user and every hostel currently
in the local database, so we can see exactly why an admin account
is landing on the "Add your hostels" onboarding screen instead of
the dashboard - instead of guessing.

Run:  python check_state.py
-------------------------------------------------------
"""

import database as db

db.init_db()
conn = db.get_connection()

print("=" * 70)
print("USERS")
print("=" * 70)
users = conn.execute(
    "SELECT user_id, username, email, role, college_name, hostel_id, floor, status FROM users"
).fetchall()
if not users:
    print("(no users)")
for u in users:
    print(dict(u))

print()
print("=" * 70)
print("HOSTELS")
print("=" * 70)
hostels = conn.execute("SELECT hostel_id, name, hostel_type, college_name FROM hostels").fetchall()
if not hostels:
    print("(no hostels)")
for h in hostels:
    print(dict(h))

print()
print("=" * 70)
print("Does each admin's college_name exactly match a hostel's college_name?")
print("=" * 70)
for u in users:
    if u["role"] != "admin":
        continue
    matches = [h for h in hostels if h["college_name"] == u["college_name"]]
    print(f"Admin '{u['email']}' -> college_name = {u['college_name']!r}")
    print(f"  Matching hostels: {len(matches)}")
    if not matches:
        close = [h["college_name"] for h in hostels]
        print(f"  Existing hostel college_name values on file: {close}")