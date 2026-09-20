# HostelOps — AI Hostel Utility Dashboard

S5 Mini Project — Full Stack + AI

Tracks electricity, water, and Wi-Fi usage per room, forecasts demand with ML,
flags anomalies, and gives admins/wardens a maintenance workflow.

## Stack

- **Frontend**: HTML/CSS/JS (`frontend/`), served directly by Flask
- **Backend**: Flask REST API (`backend_api.py`)
- **AI/ML**: scikit-learn — RandomForest (forecasting) + IsolationForest (anomalies) (`ai_models.py`)
- **DB**: SQLite (`database.py`, `hostel_data.db`)
- **Auth**: Firebase Authentication (email/password)

## Setup

```bash
pip install -r requirements.txt
python setup_project.py      # creates DB, sample data, trains models
python backend_api.py        # starts server at http://localhost:5000
```

**Before first run**, set up Firebase (required — the app won't start without it):
1. [Firebase Console](https://console.firebase.google.com/) → new project → Authentication → Email/Password → Enable
2. Project Settings → Your apps → web app → copy config into `frontend/firebase-config.js`
3. Project Settings → Service accounts → Generate private key → save as `firebase-service-account.json` in the project root
   ⚠️ Never commit this file (already in `.gitignore` — double check before your first commit)

First account you create via **Sign Up** becomes that college's admin.

## Roles

| Role | Scope |
|---|---|
| `admin` | Whole college — all hostels, user management, efficiency rankings |
| `warden` | One hostel, optionally locked to one floor |
| `staff` | Whole college, view-only (no admin actions) |

All scoping is enforced server-side in `backend_api.py`, not just hidden in the UI.

## Features

- Per-room/floor Electricity, Water, Wi-Fi — daily trend, 1-week hourly timing, 24h view
- AI demand forecasting (occupancy-driven) + anomaly detection with explanations
- Real per-room efficiency scoring
- What-If Simulator
- Maintenance ticket workflow
- Executive reports (Daily/Weekly/Monthly)
- Institution-wide hostel comparison (admin only)

## Deploying (Render)

Use **Render**, not Vercel — this needs a persistent process + local SQLite file.

1. Push to GitHub
2. Render → New Web Service → connect repo
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn backend_api:app`
3. Upload `firebase-service-account.json` via Render's **Secret Files** (not git)
4. Attach a **persistent disk** for `hostel_data.db`, or your data resets on every redeploy

## Re-train models

```bash
python ai_models.py
```

## Regenerate sample data

If you change `data_generator.py`, existing rooms' history won't update automatically:

```bash
python regenerate_readings.py
```

## Key API routes

| Route | Notes |
|---|---|
| `POST /api/signup` / `/api/login` | `{id_token}` from Firebase |
| `GET /api/rooms?hostel_id=` 🔒 | Filtered to warden's hostel/floor |
| `GET /api/electricity` `/water` `/wifi` `/occupancy` 🔒 | `?room_id=&days=` |
| `GET /api/diurnal/hourly_range?room_id=&days=` 🔒 | Hour-by-hour, up to 14 days |
| `GET /api/predict/<resource>?room_id=&n_days=` 🔒 | AI forecast |
| `GET /api/anomalies/<resource>?hostel_id=` 🔒 | Runs anomaly scan |
| `GET /api/efficiency/<resource>?hostel_id=` 🔒 | Admin + warden |
| `POST /api/wardens` 🔒👑 | `{email, password, hostel_id, floor}` |
| `GET /api/pending-wardens` / `approve` / `reject` 🔒👑 | Warden signup approval |
| `GET /api/simulator/simulate?...` 🔒 | What-If Simulator |

🔒 = needs `Authorization: Bearer <token>` · 👑 = admin only

## Notes for report

- Firebase handles identity; our own `users` table handles role/college/hostel/floor
- College = a `college_name` string match, not a separate table (single-deployment scope)
- No UI yet to add individual rooms — each hostel gets 12 demo rooms on creation
- Swap SQLite → MySQL: only `database.py`'s `get_connection()` needs to change