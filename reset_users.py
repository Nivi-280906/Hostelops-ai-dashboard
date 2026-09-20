"""
reset_users.py
-------------------------------------------------------
One-off utility: wipes every admin/warden login row (plus their
hostels/rooms and all data tied to those rooms) from the local
database so you can test signup completely from scratch.

This does NOT touch Firebase Authentication - Firebase keeps its own
separate list of accounts, so an email you already signed up with
will still say "auth/email-already-in-use" on the client even after
this script runs. To fully reset, also delete the users manually in
Firebase Console -> Authentication -> Users (select the row -> the
"..." menu -> Delete account) for each email you want to reuse.

Run:  python reset_users.py
-------------------------------------------------------
"""

import database as db

conn = db.get_connection()
cur = conn.cursor()

# Deletes must go child -> parent, since PRAGMA foreign_keys = ON
# rejects deleting a row that something else still points to
# (this is exactly what caused the "FOREIGN KEY constraint failed"
# error - hostels/rooms were being deleted before the rows that
# reference them).
cur.execute("DELETE FROM hourly_readings")
cur.execute("DELETE FROM electricity_readings")
cur.execute("DELETE FROM water_readings")
cur.execute("DELETE FROM wifi_usage")
cur.execute("DELETE FROM occupancy_log")
cur.execute("DELETE FROM alerts")
cur.execute("DELETE FROM maintenance_issues")
cur.execute("DELETE FROM notifications")
cur.execute("DELETE FROM recommendations")
cur.execute("DELETE FROM rooms")
cur.execute("DELETE FROM hostels")
cur.execute("DELETE FROM users")
conn.commit()

print("Done. All local users, hostels, rooms, and their data have been deleted.")
print("Remember: also delete the matching accounts in Firebase Console ->")
print("Authentication -> Users if you want to reuse the same email addresses.")