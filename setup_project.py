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
-------------------------------------------------------
"""

import database as db
import data_generator as gen
import ai_models as ai
import auth


def main():
    print("=" * 60)
    print("STEP 1/4: Initializing database schema...")
    db.init_db()

    print("=" * 60)
    print("STEP 2/4: Seeding default login accounts...")
    auth.seed_default_users()

    print("=" * 60)
    print("STEP 3/4: Generating synthetic hostel sensor data...")
    gen.reset_and_seed()

    print("=" * 60)
    print("STEP 4/4: Training AI models (forecasting + anomaly detection)...")
    ai.train_all_models()
    ai.detect_anomalies("electricity")
    ai.detect_anomalies("water")
    ai.detect_anomalies("wifi")

    print("=" * 60)
    print("SETUP COMPLETE \u2705")
    print("Now run:")
    print("  python backend_api.py")
    print("Then open:  http://localhost:5000")
    print("=" * 60)


if __name__ == "__main__":
    main()
