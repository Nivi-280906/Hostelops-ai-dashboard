"""
auth.py
-------------------------------------------------------
Authentication layer for the Hostel Utility Dashboard.

Identity (who you are) is handled entirely by Firebase Authentication.
This file no longer stores or checks passwords itself - it:

  1. Verifies the Firebase ID token the frontend sends on every request
     (via the Firebase Admin SDK), and
  2. Maps that verified Firebase identity (by email) to our own `users`
     row, which is where role / college_name / hostel_id (our app's
     authorization data - Firebase has no concept of these) live.

Setup required (see README "Firebase setup" section):
  pip install firebase-admin
  - Create a Firebase project, enable the Email/Password sign-in
    provider, and download a service-account JSON key.
  - Put that key's path in the FIREBASE_SERVICE_ACCOUNT_KEY env var,
    or a file named firebase-service-account.json next to this file.
-------------------------------------------------------
"""

import os
import re

import firebase_admin
from firebase_admin import credentials, auth as firebase_auth

import database as db
import data_generator

_CRED_PATH = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY", "firebase-service-account.json")


def _ensure_firebase_app():
    """Initializes the Firebase Admin SDK on first use (not at import time),
    so scripts that import this module without needing auth - like
    setup_project.py's DB/data/AI steps - don't require Firebase to be
    configured yet."""
    if firebase_admin._apps:
        return
    if not os.path.exists(_CRED_PATH):
        raise RuntimeError(
            f"Firebase service account key not found at '{_CRED_PATH}'. "
            "Download it from Firebase Console -> Project Settings -> "
            "Service accounts -> Generate new private key, save it there, "
            "or point FIREBASE_SERVICE_ACCOUNT_KEY at it."
        )
    try:
        firebase_admin.initialize_app(credentials.Certificate(_CRED_PATH))
    except Exception as e:
        # Catches ValueError / malformed-key errors too - e.g. the file still
        # has the "PASTE_YOUR_..." placeholder values instead of a real key.
        raise RuntimeError(
            f"Firebase service account key at '{_CRED_PATH}' could not be loaded "
            f"({e}). Make sure it's the real key downloaded from Firebase Console "
            "-> Project Settings -> Service accounts -> Generate new private key, "
            "not the placeholder file."
        )

# A placeholder value only - the real password lives in Firebase, not here.
# The DB column is NOT NULL so we still need to put *something* in it.
_NO_LOCAL_PASSWORD = "firebase-managed"

DEMO_COLLEGE = "Demo College"


def _derive_college_name(email):
    """Turns 'ada.lovelace@gmail.com' into 'Ada Lovelace's College' - just a
    friendly starting name, the admin can add real hostels right after."""
    local_part = email.split("@", 1)[0]
    label = re.sub(r"[._\-+]+", " ", local_part).strip().title()
    return f"{label or 'New'}'s College"


def _session_payload(user):
    return {
        "username": user["username"],
        "role": user["role"],
        "email": user.get("email"),
        "college_name": user.get("college_name"),
        "hostel_id": user.get("hostel_id"),
        "floor": user.get("floor"),
    }


def verify_id_token(id_token):
    """Verifies a Firebase ID token. Returns the decoded token dict, or None
    if it's missing, malformed, expired, or otherwise invalid."""
    if not id_token:
        return None
    _ensure_firebase_app()
    try:
        return firebase_auth.verify_id_token(id_token)
    except Exception as e:
        # Don't swallow the real reason - "Invalid or expired sign-in" was
        # showing for every kind of failure (clock skew, no internet to
        # reach Google's cert endpoint, bad service-account credentials,
        # an actually-expired token, ...). Print it so it's visible in the
        # terminal running backend_api.py.
        print(f"[auth] Firebase token verification failed: {type(e).__name__}: {e}")
        return None


def sync_signup(id_token, college_name, hostel_name=None, hostel_type=None):
    """Called right after the frontend does firebase.auth()
    .createUserWithEmailAndPassword() for a brand-new college admin.
    Verifies the token, then creates our local app-level row (role,
    college_name) for that Firebase identity, plus the college's first
    hostel (collected on the same signup form) so the admin lands
    straight on the dashboard instead of a separate onboarding step."""
    decoded = verify_id_token(id_token)
    if not decoded:
        return {"error": "Could not verify your sign-in. Please try again."}

    email = (decoded.get("email") or "").strip().lower()
    if not email:
        return {"error": "Your Firebase account has no email on it."}
    if db.get_user_by_email(email):
        return {"error": "An account with that email already exists"}

    college_name = (college_name or "").strip() or _derive_college_name(email)
    user_id = db.insert_user(
        username=email,
        password_hash=_NO_LOCAL_PASSWORD,
        role="admin",
        email=email,
        college_name=college_name,
    )
    hostel_name = (hostel_name or "").strip()
    if hostel_name:
        hostel_id = db.insert_hostel(college_name, hostel_name, hostel_type or "co-ed")
        # Match what the onboarding "+ Add Hostel" screen does - generate real
        # rooms + simulated history right away so the dashboard isn't just
        # zeros the moment you land on it.
        data_generator.seed_demo_hostel(hostel_id)
    user = db.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return _session_payload(user)


def sync_login(id_token):
    """Called right after the frontend does firebase.auth()
    .signInWithEmailAndPassword(). Verifies the token and looks up the
    matching local app-level row.

    Deliberately does NOT auto-create an account here. Local development and
    a deployed environment (e.g. Render) use separate databases but the same
    Firebase project, so a Firebase login created in one environment still
    verifies successfully in the other - auto-provisioning a brand-new admin
    for any such "orphaned" Firebase identity silently handed out full admin
    access to whoever happened to sign in first, which is how a warden's
    login on a fresh deployment could end up as an unrelated phantom admin.
    Account creation only happens through the explicit /api/signup and
    /api/warden-signup flows now.
    """
    decoded = verify_id_token(id_token)
    if not decoded:
        return {"error": "Invalid or expired sign-in. Please log in again."}

    email = (decoded.get("email") or "").strip().lower()
    if not email:
        return {"error": "Your Firebase account has no email on it."}

    user = db.get_user_by_email(email)
    if not user:
        return {"error": "No account found for this email in this environment. Please sign up first."}
    if user["status"] == "PENDING":
        return {"error": "Your warden account is still awaiting approval from your college admin."}
    return _session_payload(user)


def warden_signup(id_token, college_name, hostel_id, floor):
    """Called right after the frontend does firebase.auth()
    .createUserWithEmailAndPassword() for a warden requesting access to one
    floor of one hostel. Unlike sync_signup, this does NOT return a usable
    session - the row is created with status='PENDING' and stays that way
    until the college admin approves it (see approve-warden endpoint)."""
    decoded = verify_id_token(id_token)
    if not decoded:
        return {"error": "Could not verify your sign-in. Please try again."}

    email = (decoded.get("email") or "").strip().lower()
    if not email:
        return {"error": "Your Firebase account has no email on it."}
    if db.get_user_by_email(email):
        return {"error": "An account with that email already exists"}

    college_name = (college_name or "").strip()
    if not college_name:
        return {"error": "College name is required"}

    hostel = db.get_hostel(hostel_id) if hostel_id else None
    if not hostel or hostel["college_name"].strip().lower() != college_name.lower():
        return {"error": "That hostel doesn't belong to the college you entered. Check the name and try again."}

    if not floor and floor != 0:
        return {"error": "Floor is required"}

    db.insert_user(
        username=email,
        password_hash=_NO_LOCAL_PASSWORD,
        role="warden",
        email=email,
        college_name=hostel["college_name"],  # exact stored spelling, not what the visitor typed
        hostel_id=hostel_id,
        floor=floor,
        status="PENDING",
    )
    return {"status": "pending"}


def get_session_from_token(id_token):
    """Used by @require_auth on every protected request: verifies the
    Firebase ID token fresh (tokens are short-lived and the frontend SDK
    auto-refreshes them) and returns the matching local session dict, or
    None if the token is invalid or the identity has no local app row."""
    decoded = verify_id_token(id_token)
    if not decoded:
        return None
    email = (decoded.get("email") or "").strip().lower()
    if not email:
        return None
    return db.get_user_by_email(email)


def create_warden(college_name, hostel_id, email, password, floor=None):
    """College admin action: creates a login locked to one specific hostel
    (and, optionally, one floor within it). Creates the real Firebase Auth
    account server-side (via the Admin SDK) *and* the local app-level row
    (role=warden, hostel_id, floor) in one step."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return {"error": "A valid email is required"}
    if not password or len(password) < 6:
        return {"error": "Password must be at least 6 characters"}
    if db.get_user_by_email(email):
        return {"error": "An account with that email already exists"}

    _ensure_firebase_app()
    try:
        firebase_auth.create_user(email=email, password=password)
    except firebase_auth.EmailAlreadyExistsError:
        return {"error": "An account with that email already exists in Firebase"}
    except Exception as e:
        return {"error": f"Could not create the Firebase account: {e}"}

    user_id = db.insert_user(
        username=email,
        password_hash=_NO_LOCAL_PASSWORD,
        role="warden",
        email=email,
        college_name=college_name,
        hostel_id=hostel_id,
        floor=floor,
    )
    return db.fetch_one(
        "SELECT user_id, username, role, email, college_name, hostel_id, floor, created_at FROM users WHERE user_id = ?",
        (user_id,),
    )


def delete_user_account(user):
    """Admin action: removes a user's local app row and, if a matching
    Firebase Auth account exists for their email, removes that too (so a
    removed account can't just log back in and get auto-provisioned again
    by sync_login). Missing/inconsistent Firebase state is tolerated -
    the local removal always goes through."""
    email = user.get("email")
    if email:
        try:
            _ensure_firebase_app()
            fb_user = firebase_auth.get_user_by_email(email)
            firebase_auth.delete_user(fb_user.uid)
        except firebase_auth.UserNotFoundError:
            pass
        except Exception:
            # Firebase not configured, or a transient error - don't block
            # removing the local row over it.
            pass
    db.delete_user(user["user_id"])


def seed_default_users():
    """No-op under Firebase auth: demo accounts must exist as real Firebase
    users (create them in the Firebase Console or via the Sign Up screen),
    since we can no longer fabricate a working password locally."""
    return