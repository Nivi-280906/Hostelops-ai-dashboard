"""
test_backend_routes.py
-------------------------------------------------------
Smoke-tests every route EXCEPT the actual Firebase handshake (that part
needs a real Firebase project + real ID token, which this local script
doesn't have). We monkeypatch auth.get_session_from_token() to hand back
a fixed demo session for a fake test token, so everything downstream of
login - the part this project's own code owns - still gets exercised.
-------------------------------------------------------
"""

import json
import database as db
import auth
import backend_api

db.init_db()

# Make sure a demo admin row exists to attach the fake session to.
existing = db.get_user_by_email("admin@demo.edu")
if not existing:
    db.insert_user("admin@demo.edu", "firebase-managed", role="admin",
                    email="admin@demo.edu", college_name="Demo College")

FAKE_TOKEN = "TEST-FAKE-TOKEN"
_real_get_session_from_token = auth.get_session_from_token


def _fake_get_session_from_token(id_token):
    if id_token == FAKE_TOKEN:
        return db.get_user_by_email("admin@demo.edu")
    return _real_get_session_from_token(id_token)


auth.get_session_from_token = _fake_get_session_from_token

client = backend_api.app.test_client()
headers = {"Authorization": f"Bearer {FAKE_TOKEN}"}
print("Faked-auth session OK (real login needs a live Firebase project)!")

# Test: Overview KPIs
res = client.get("/api/overview/kpis", headers=headers)
assert res.status_code == 200, f"Overview KPIs failed: {res.data}"
kpis = res.get_json()
print("Overview KPIs keys:", list(kpis.keys()))
assert "electricity" in kpis and "cost" in kpis and "efficiency" in kpis

# Test: Hostels comparison
res = client.get("/api/hostels/comparison", headers=headers)
assert res.status_code == 200, f"Hostels comparison failed: {res.data}"
comp = res.get_json()
print("Hostels comparison count:", len(comp) if isinstance(comp, list) else len(comp.get("hostels", [])))

# Test: Occupancy prediction
res = client.get("/api/predict/occupancy?n_days=7", headers=headers)
assert res.status_code == 200, f"Predict occupancy failed: {res.data}"
print("Occupancy prediction OK!")

# Test: Simulator
res = client.get("/api/simulator/simulate?elec_reduction_pct=10&water_reduction_pct=5", headers=headers)
assert res.status_code == 200, f"Simulator failed: {res.data}"
sim = res.get_json()
print("Simulator impact:", sim.get("impact"))

# Test: Recommendations
res = client.get("/api/recommendations", headers=headers)
assert res.status_code == 200, f"Recommendations failed: {res.data}"
recs = res.get_json()
print("Recommendations count:", len(recs))

# Test: Maintenance issue lifecycle
rooms = db.get_rooms()
hostels = db.get_hostels("Demo College")
h_id = hostels[0]["hostel_id"]
r_id = rooms[0]["room_id"]

res = client.post("/api/maintenance", headers=headers, json={
    "hostel_id": h_id,
    "room_id": r_id,
    "resource_type": "water",
    "problem": "Leaking faucet in washroom",
    "severity": "high",
    "description": "Continuous dripping sound noticed during evening inspection",
})
assert res.status_code == 201, f"Create maintenance failed: {res.data}"
created_issue = res.get_json()
issue_id = created_issue["issue_id"]
print("Created maintenance issue ID:", issue_id)

# Update issue to IN_PROGRESS
res = client.patch(f"/api/maintenance/{issue_id}", headers=headers, json={
    "status": "IN_PROGRESS",
    "resolution_notes": "Plumber dispatched with spare valve",
})
assert res.status_code == 200
print("Updated issue to IN_PROGRESS OK!")

# Test: Notifications
res = client.get("/api/notifications", headers=headers)
assert res.status_code == 200
notes = res.get_json()
print("Notifications count:", len(notes))

# Test: Chatbot query
res = client.post("/api/chatbot/query", headers=headers, json={"query": "Which hostel has the highest electricity consumption?"})
assert res.status_code == 200
chat_res = res.get_json()
print("Chatbot response:", chat_res.get("response"))

# Test: Demo event simulation
res = client.post("/api/demo/simulate_event", headers=headers, json={"event_type": "water_leak", "hostel_id": h_id, "room_id": r_id})
assert res.status_code == 200
demo_res = res.get_json()
print("Demo event result:", demo_res.get("xai_explanation"))

# Test: Reports generation
res = client.get("/api/reports/generate?period=monthly", headers=headers)
assert res.status_code == 200
rep = res.get_json()
print("Report generation OK! Financial total:", rep.get("financials", {}).get("total_cost"))

# Test: Real-Time 24-Hour Diurnal Academic Schedule Profile
res = client.get(f"/api/diurnal/hourly?room_id={r_id}", headers=headers)
assert res.status_code == 200, f"Diurnal profile failed: {res.data}"
diurnal_res = res.get_json()
assert len(diurnal_res.get("hours", [])) == 24, "Must have 24 hours"
print("24-Hour Academic Diurnal Profile OK! Hours:", len(diurnal_res["hours"]))
print("Academic insights:", diurnal_res.get("academic_insights"))

print("\nALL BACKEND ROUTE TESTS PASSED (auth handshake itself needs a real Firebase project to test live)!")
