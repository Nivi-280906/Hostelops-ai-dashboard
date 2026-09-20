# HostelOps — AI-Driven Hostel Utility Optimization & Predictive Resource Management

S5 Mini Project — Full Stack + AI

A live dashboard that tracks electricity, water, and Wi-Fi usage per room across a
college's hostels, forecasts demand with a trained ML model, flags anomalies
(leaks, appliances left on, bandwidth abuse) with plain-language explanations, and
gives admins/wardens a closed-loop workflow from detection → maintenance ticket →
resolution.

## Highlights

- **Per-room and per-floor monitoring** — Electricity/Water/Wi-Fi with three time
  views: a 60–150 day daily trend, a 1-week hour-by-hour "timing" view (real stored
  hourly data, not interpolated), and a single-day 24h academic-routine breakdown.
- **AI forecasting** — `RandomForestRegressor` per resource, driven primarily by a
  predicted-occupancy model (Occupancy Forecast → Resource Forecast → Cost Impact),
  with a standby-power floor so a forecast day with 0 predicted occupants actually
  drops toward near-zero usage instead of tracking the recent occupied-day average.
- **Anomaly detection** — `IsolationForest`, unsupervised, with direction-aware
  explanations (a usage *drop* and a usage *spike* get different probable causes —
  e.g. "room vacant" vs. "appliance left on").
- **Real per-room efficiency scoring** — ranks rooms by consumption-per-occupant
  with an actual computed score/grade/reason per room (not a placeholder).
- **What-If Simulator** — models occupancy and conservation-target changes against
  real baseline data, with correctly-signed savings/loss indicators.
- **Role-based access, enforced server-side** — Admin (whole college), Warden
  (locked to one hostel, optionally one specific floor within it), Staff. A
  floor-scoped warden's room list, alerts, KPIs, and efficiency rankings are all
  filtered server-side — not just hidden in the UI.
- **Closed-loop maintenance** — anomaly → ticket → assign → in progress → resolved,
  with an "AI Demonstration Mode" to inject controlled sensor spikes live for a
  viva/review presentation.
- **Executive reports** — Daily/Weekly/Monthly audit-style PDFs-in-browser, printable.

## Architecture

```
┌───────────────────────┐      HTTP (REST, polling)     ┌──────────────────────┐      function calls     ┌──────────────────────┐
│   FRONTEND              │  ───────────────────────────▶  │   BACKEND               │  ─────────────────────▶ │   AI / ML MODULE        │
│   frontend/ (HTML/CSS/JS) │  ◀───────────────────────────  │   backend_api.py         │  ◀───────────────────── │   ai_models.py            │
│   served by Flask itself  │              JSON              │   (Flask REST API)       │        results           │   (scikit-learn)          │
└───────────────────────┘                                └──────────────────────┘                         └──────────────────────┘
                                                                       │
                                                                       ▼
                                                              ┌──────────────────────┐
                                                              │   database.py            │
                                                              │   (SQLite)                 │
                                                              └──────────────────────┘
```

- **Frontend ("HostelOps" live dashboard)** — `frontend/` (`index.html`, `style.css`,
  `app.js`, vendored Chart.js). A real-time single-page app: polls the backend every
  10–60s (adjustable in the UI), auto-updating KPI cards, live charts, and an alert
  feed without a page reload. Served directly by Flask, so there's no separate
  frontend server and no CORS setup needed for normal use.
- **Backend (Flask REST API)** — `backend_api.py`. Exposes `/api/...` endpoints,
  reads from the DB, calls the AI module for predictions/anomalies, and serves the
  frontend's static files. Every data endpoint requires a valid Firebase ID token;
  some additionally require the `admin` role.
- **Auth module** — `auth.py`. Verifies Firebase ID tokens (via the Firebase Admin
  SDK) and maps them to our own `users` row for role/college/hostel/floor.
- **AI/ML module** — `ai_models.py`. Independently runnable/testable with
  `python ai_models.py`.
- **Database** — `database.py` + `hostel_data.db` (SQLite, auto-created).
- **Data generator** — `data_generator.py`. Simulates smart-meter sensor data with
  realistic weekday/weekend occupancy patterns and intentionally injected anomalies.
  If you ever change this file's logic, run `python regenerate_readings.py` to
  regenerate existing rooms' history to match — editing the generator doesn't
  retroactively change data already sitting in `hostel_data.db`.

## What the AI actually does

1. **Occupancy forecasting** — predicts near-term room occupancy from day-of-week
   patterns and recent trend; this feeds into demand forecasting as the primary driver.
2. **Consumption forecasting** — `RandomForestRegressor` trained per resource on
   day of week, month, occupancy, room capacity, occupancy ratio, previous day's
   usage (lag), and 7-day rolling average, generated recursively for the next N days.
   Blended with a resource-specific standby-power floor so near-zero occupancy days
   forecast near-zero (not "recent average") consumption.
3. **Anomaly detection** — `IsolationForest` flags readings that deviate sharply
   from a room's normal pattern, with direction-aware causes/actions (spike vs. drop
   get different, physically sensible explanations) written to an `alerts` table.
4. **Room efficiency ranking** — real per-room score/grade/reason based on actual
   consumption-per-occupant, not a fixed placeholder value.

## Authentication & Roles

The dashboard uses a **college → hostel → (optional floor) → user** structure: one
college signs up, adds its hostels, and creates logins scoped to that college, to
one hostel, or to one floor within a hostel.

**Getting started (first-time college signup):**

1. On the login screen, click **Sign Up (new college)** and enter your college name,
   an email, and a password. This creates the college's first **admin** account and
   logs you straight in.
2. Since the college has no hostels yet, you land on an **onboarding screen**: add
   each hostel by name and type (girls / boys / co-ed). Each hostel is seeded with a
   working set of rooms and realistic reading history the moment it's created.
3. Click **Continue to Dashboard**. The admin can switch between all of the
   college's hostels via the **Hostel** dropdown, and manage everything from the
   **Admin** panel (add/remove hostels, create warden logins).

**Three roles, enforced server-side (not just hidden in the UI):**

| Role | Scope | Can do |
|---|---|---|
| `admin` | Whole college | Everything: switch between all hostels, add/remove hostels, create warden accounts (optionally floor-scoped), view efficiency rankings, institution-wide comparison |
| `warden` | One hostel, optionally one floor within it | Signs in directly to their assigned scope — no hostel switcher, no onboarding, no admin panel, no institution-wide comparison. Every room list, alert feed, KPI card, and efficiency ranking is filtered to their hostel (and floor, if set) |
| `staff` | Whole college (legacy/demo role) | Same view access as admin, minus admin-only actions |

A warden's lock to their hostel/floor is enforced in `backend_api.py` itself —
every route that takes a `room_id`, `hostel_id`, or builds a room list re-resolves
it against the session (`_hostel_scoped()`, `_room_scoped()`, `_caller_room_ids()`,
`_apply_floor_scope()`), so a warden hitting another hostel's or floor's `room_id`
directly (e.g. via `curl`) gets `404`/a filtered result, not just a hidden dropdown.

**Admin creates warden logins** from the Admin panel → **Create Warden Login**: pick
a hostel, optionally pick a specific floor (or leave "All Floors" for whole-hostel
access), enter an email + password.

Session mechanics — **backed by Firebase Authentication**:

1. The frontend calls Firebase's `signInWithEmailAndPassword()` /
   `createUserWithEmailAndPassword()` directly — Firebase checks the password and
   issues a short-lived ID token. The backend never sees or stores passwords.
2. The frontend sends `id_token` to `POST /api/login` or `POST /api/signup`, which
   verifies it via the Firebase Admin SDK and creates/looks up the matching row in
   our own `users` table (role/college_name/hostel_id/floor).
3. Every subsequent API call sends a fresh ID token as `Authorization: Bearer
   <id_token>`. `@require_auth()` re-verifies it on **every single request**.
4. Sign-out happens client-side via `firebase.auth().signOut()`.

**Firebase setup (do this once, before first run):**

1. [Firebase Console](https://console.firebase.google.com/) → create a project →
   **Build → Authentication → Get started → Sign-in method → Email/Password → Enable**.
2. **Frontend config:** Project Settings → General → "Your apps" → web icon (`</>`)
   → register an app → copy the `firebaseConfig` object into
   `frontend/firebase-config.js`.
3. **Backend config:** Project Settings → Service accounts → **Generate new private
   key** → save as `firebase-service-account.json` in the project root, or point
   `FIREBASE_SERVICE_ACCOUNT_KEY` at it.
   ⚠️ **Never commit this file** — it's a real credential. Check your `.gitignore`
   actually matches its exact filename before your first commit.
4. `pip install -r requirements.txt`.
5. Create your first account from the **Sign Up** tab — it becomes that college's
   first admin automatically.

## Setup & Run (local development)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. One-time setup: creates DB, generates synthetic sensor data, trains AI models
python setup_project.py

# 3. Start the backend (this also serves the frontend - one process, one port)
python backend_api.py

# 4. Open the dashboard
#    http://localhost:5000
```

## Deploying (Render)

This app is a stateful Flask server with a local SQLite file — it's built to run as
a normal persistent process, which is what **Render** (not Vercel/serverless) is for.

1. Push this repo to GitHub (see `.gitignore` note above — never commit
   `firebase-service-account.json`).
2. On Render: **New → Web Service** → connect your repo.
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `gunicorn backend_api:app`
3. **Environment variables:** none are strictly required for the app to boot, but
   set `SMTP_EMAIL` / `SMTP_APP_PASSWORD` if you want warden-approval emails to send.
4. **Firebase service account:** use Render's **Secret Files** feature to upload
   `firebase-service-account.json` rather than committing it — the code already
   reads `FIREBASE_SERVICE_ACCOUNT_KEY` (or the default filename) via
   `auth.py`, no code change needed.
5. **Persistent data:** Render's disk resets on every redeploy unless you attach a
   **persistent disk** (Render dashboard → your service → Disks) and point
   `hostel_data.db` at a path on it — otherwise your data resets on each deploy.
6. `backend_api.py` already detects Render's `$PORT` env var and binds `0.0.0.0`
   automatically; the Werkzeug debug console is disabled outside local dev.

## Re-training the AI models later

```bash
python ai_models.py
```
Retrains both models on whatever data is currently in `hostel_data.db` and re-runs anomaly detection.

## API Reference (for your report / viva)

| Endpoint | Description |
|---|---|
| `GET /` | Serves the dashboard (`frontend/index.html`) |
| `GET /api/health` | Health check (unauthenticated) |
| `POST /api/signup` | `{college_name, id_token}` → creates the college + its first admin |
| `POST /api/login` | `{id_token}` → `{username, role, college_name, hostel_id, floor}` |
| `POST /api/logout` | No-op; sign-out happens client-side |
| `GET /api/me` 🔒 | Current logged-in user's identity |
| `GET /api/hostels` 🔒 | List hostels — all of the caller's college for admin/staff, just their own for a warden |
| `POST /api/hostels` 🔒👑 | `{name, hostel_type}` → creates a hostel + seeds demo rooms/readings |
| `DELETE /api/hostels/<id>` 🔒👑 | Removes a hostel and cascades to its rooms/readings/alerts |
| `GET /api/users` 🔒👑 | List **active** accounts in the caller's college (pending warden requests are excluded — see Pending Wardens) |
| `GET /api/pending-wardens` 🔒👑 | List warden self-signup requests awaiting approval |
| `POST /api/pending-wardens/<id>/approve` 🔒👑 | Activates a pending warden account |
| `POST /api/pending-wardens/<id>/reject` 🔒👑 | Deletes a pending warden request |
| `POST /api/wardens` 🔒👑 | `{email, password, hostel_id, floor}` → create a login locked to one hostel (and optionally one floor) |
| `GET /api/rooms?hostel_id=` 🔒 | List rooms — filtered to the warden's hostel and floor, regardless of the param |
| `GET /api/electricity?room_id=&days=` 🔒 | Raw electricity readings (404 if the room isn't yours to see) |
| `GET /api/water?room_id=&days=` 🔒 | Raw water readings |
| `GET /api/wifi?room_id=&days=` 🔒 | Raw WiFi usage readings — data_gb + connected_devices |
| `GET /api/occupancy?room_id=&days=` 🔒 | Raw occupancy log |
| `GET /api/diurnal/hourly?room_id=` 🔒 | Single-day 24-hour breakdown (real stored hourly data) |
| `GET /api/diurnal/hourly_range?room_id=&days=` 🔒 | Multi-day hour-by-hour "timing" view (up to 14 days of real stored hourly data) |
| `GET /api/predict/<electricity\|water\|wifi>?room_id=&n_days=` 🔒 | AI forecast |
| `GET /api/predict/occupancy?room_id=&n_days=` 🔒 | AI occupancy forecast |
| `GET /api/anomalies/<electricity\|water\|wifi>?recent_days=&hostel_id=` 🔒 | Runs the AI anomaly scan |
| `GET /api/efficiency/<electricity\|water\|wifi>?hostel_id=` 🔒 | Real per-room efficiency ranking (admin **and** warden) |
| `GET /api/alerts?resource_type=&limit=&hostel_id=` 🔒 | Stored alert history |
| `GET /api/overview/kpis?hostel_id=&room_id=` 🔒 | Main dashboard KPI cards, optionally scoped to one room |
| `GET /api/simulator/simulate?hostel_id=&elec_reduction_pct=&water_reduction_pct=&wifi_reduction_pct=&occupancy=` 🔒 | What-If Optimization Simulator |
| `GET /api/reports/generate?period=&hostel_id=` 🔒 | Daily/Weekly/Monthly report data |

🔒 = requires `Authorization: Bearer <token>` from `/api/login` or `/api/signup`, returns `401` otherwise.
👑 = additionally requires the `admin` role, returns `403` otherwise.

## Known simplifications (worth stating in your report)

- Firebase issues and verifies identity; our own `users` table is still the source
  of truth for authorization (role/college/hostel/floor), joined by email.
- A college is identified by its `college_name` string (not a separate `colleges`
  table) — anyone who signs up with the exact same name joins that same college.
- No UI yet to add individual rooms to a hostel after creation — each new hostel
  gets a fixed demo set of 12 rooms with simulated sensor history.
- Session storage is in-memory; a backend restart requires re-login for anyone
  whose token isn't still valid client-side.

## Swapping SQLite for MySQL (optional, if your report requires it)

Only `database.py`'s `get_connection()` needs to change — everything else (backend,
frontend, AI) is unaffected since they all go through the helper functions in
`database.py`.

## Extending this for a real deployment

- Replace `data_generator.py` with an MQTT/serial listener reading real smart-meter data.
- Move session storage from the in-memory `auth.SESSIONS` dict to a DB-backed table
  or Redis so logins survive a backend restart, and add rate-limiting on
  `/api/login` before exposing it beyond a trusted network.
- Schedule `ai_models.detect_anomalies()` and `train_all_models()` with a cron job /
  APScheduler for continuous monitoring instead of manual runs.