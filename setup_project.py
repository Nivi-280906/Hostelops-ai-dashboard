"""
setup_project.py
-------------------------------------------------------
Run this ONCE before starting the backend/frontend.
It wires together every module (database -> data_generator -> ai_models)
so the whole pipeline runs end-to-end with a single command:

    python setup_project.py

After this finishes, run:
    python backend_api.py
Then open:  http://localhost:5000

Note: AI model training is intentionally NOT run here. At this point
the database is empty by design (hostels/rooms are only created later,
via the Sign Up / onboarding flow in auth.py + backend_api.py). Training
models against an empty occupancy_log crashes with a KeyError. Models
are instead trained/updated on demand once real hostel data exists
(see ai.detect_anomalies calls in backend_api.py).
-------------------------------------------------------
"""

import database as db
import data_generator as gen
import auth


def main():
    print("=" * 60)
    print("STEP 1/3: Initializing database schema...")
    db.init_db()

    print("=" * 60)
    print("STEP 2/3: Seeding default login accounts...")
    auth.seed_default_users()

    print("=" * 60)
    print("STEP 3/3: Generating synthetic hostel sensor data...")
    gen.reset_and_seed()

    print("=" * 60)
    print("SETUP COMPLETE \u2705")
    print("Now run:")
    print("  python backend_api.py")
    print("Then open:  http://localhost:5000")
    print("=" * 60)


if __name__ == "__main__":
    main()