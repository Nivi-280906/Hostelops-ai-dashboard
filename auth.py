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
-------------------------------------------------------
"""

import json
import os
import re
import sqlite3

import firebase_admin
from firebase_admin import credentials, auth as firebase_auth

import database as db
import data_generator

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Where to look for the service-account key, in order. Resolved relative to
# THIS file (not the working directory) because gunicorn on Render does not
# necessarily start in the project folder. /etc/secrets/ is where Render
# mounts "Secret Files".
_CRED_CANDIDATES = [
    os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY"),
    os.path.join(_BASE_DIR, "firebase-service-account.json"),
    "/etc/secrets/firebase-service-account.json",
    "firebase-service-account.json",
]
_CRED_PATH = next((p for p in _CRED_CANDIDATES if p and os.path.exists(p)),
                  os.path.join(_BASE_DIR, "firebase-service-account.json"))

# Last reason a token check failed - shown in the API error message.
LAST_VERIFY_ERROR = None


def _load_credentials():
    """Builds Firebase credentials from, in order:
      1. FIREBASE_SERVICE_ACCOUNT_JSON (raw JSON text in an env var -
         easiest on Render: no file needed)
      2. a key file (see _CRED_CANDIDATES)."""
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        try:
            info = json.loads(raw)
            # Env vars often turn the key's real newlines into literal "\n".
            if isinstance(info.get("private_key"), str):
                info["private_key"] = info["private_key"].replace("\\n", "\n")
            return credentials.Certificate(info)
        except Exception as e:
            raise RuntimeError(
                f"FIREBASE_SERVICE_ACCOUNT_JSON is set but is not a valid "
                f"service-account key ({type(e).__name__}: {e})."
            )
    if not os.path.exists(_CRED_PATH):
        raise RuntimeError(
            f"Firebase service account key not found (looked at '{_CRED_PATH}' and "
            "/etc/secrets/firebase-service-account.json). Locally: put the key file next "
            "to auth.py. On Render: add it as a Secret File named "
            "firebase-service-account.json, or paste its contents into an environment "
            "variable called FIREBASE_SERVICE_ACCOUNT_JSON."
        )
    try:
        return credentials.Certificate(_CRED_PATH)
    except Exception as e:
        raise RuntimeError(
            f"Firebase service account key at '{_CRED_PATH}' could not be loaded "
            f"({e}). Make sure it's the real key downloaded from Firebase Console "
            "-> Project Settings -> Service accounts -> Generate new private key, "
            "not a placeholder file."
        )


def _ensure_firebase_app():
    """Initializes the Firebase Admin SDK on first use (not at import time)."""
    if firebase_admin._apps:
        return
    firebase_admin.initialize_app(_load_credentials())


def firebase_status():
    """Used by /api/health so a broken Firebase setup is visible immediately."""
    try:
        _ensure_firebase_app()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# A placeholder value only - the real password lives in Firebase, not here.
_NO_LOCAL_PASSWORD = "firebase-managed"

DEMO_COLLEGE = "Demo College"


def _derive_college_name(email):
    """Turns 'ada.lovelace@gmail.com' into 'Ada Lovelace's College'."""
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
    if it's missing, malformed, expired, or otherwise invalid. The reason for
    a failure is kept in LAST_VERIFY_ERROR and printed to the server log."""
    global LAST_VERIFY_ERROR
    LAST_VERIFY_ERROR = None
    if not id_token:
        LAST_VERIFY_ERROR = "No sign-in token was sent."
        return None
    _ensure_firebase_app()   # RuntimeError (config problem) propagates on purpose
    try:
        # clock_skew_seconds: a server clock a few seconds behind Google's
        # otherwise rejects a brand-new token with "Token used too early" -
        # exactly what a fresh login/signup is.
        return firebase_auth.verify_id_token(id_token, clock_skew_seconds=60)
    except Exception as e:
        LAST_VERIFY_ERROR = f"{type(e).__name__}: {e}"
        print(f"[auth] Firebase token verification failed: {LAST_VERIFY_ERROR}")
        return None


def _verify_failed_message(default):
    """Adds the real reason (if known) to a generic 'could not verify' message."""
    return f"{default} ({LAST_VERIFY_ERROR})" if LAST_VERIFY_ERROR else default


def sync_signup(id_token, college_name, hostel_name=None, hostel_type=None):
    """Creates the local admin row (+ first hostel) after Firebase signup.
    All-or-nothing: if anything fails after the user row is created, the
    partial rows are removed so the person can simply retry."""
    decoded = verify_id_token(id_token)
    if not decoded:
        return {"error": _verify_failed_message("Could not verify your sign-in. Please try again.")}

    email = (decoded.get("email") or "").strip().lower()
    if not email:
        return {"error": "Your Firebase account has no email on it."}
    if db.get_user_by_email(email):
        return {"error": "An account with that email already exists"}

    college_name = (college_name or "").strip() or _derive_college_name(email)
    hostel_name = (hostel_name or "").strip()

    # Validate BEFORE creating anything.
    if hostel_name:
        clash = db.fetch_one(
            "SELECT hostel_id FROM hostels WHERE LOWER(TRIM(college_name)) = LOWER(TRIM(?)) "
            "AND LOWER(TRIM(name)) = LOWER(TRIM(?))",
            (college_name, hostel_name),
        )
        if clash:
            return {"error": f"'{hostel_name}' already exists for {college_name}. "
                             "Use a different hostel name, or ask that college's admin for access."}

    try:
        user_id = db.insert_user(
            username=email,
            password_hash=_NO_LOCAL_PASSWORD,
            role="admin",
            email=email,
            college_name=college_name,
        )
    except sqlite3.IntegrityError:
        return {"error": "An account with that email already exists"}

    hostel_id = None
    try:
        if hostel_name:
            hostel_id = db.insert_hostel(college_name, hostel_name, hostel_type or "co-ed")
            data_generator.seed_demo_hostel(hostel_id)
    except Exception:
        # Roll back so a retry starts clean.
        if hostel_id:
            try:
                db.delete_hostel(hostel_id)
            except Exception:
                pass
        db.delete_user(user_id)
        raise
    user = db.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return _session_payload(user)


def sync_login(id_token):
    """Verifies the token and looks up the matching local app-level row.
    If this Firebase identity has never been seen before, auto-provisions a
    fresh college admin row for it."""
    decoded = verify_id_token(id_token)
    if not decoded:
        return {"error": _verify_failed_message("Invalid or expired sign-in. Please log in again.")}

    email = (decoded.get("email") or "").strip().lower()
    if not email:
        return {"error": "Your Firebase account has no email on it."}

    user = db.get_user_by_email(email)
    if user:
        if user["status"] == "PENDING":
            return {"error": "Your warden account is still awaiting approval from your college admin."}
        return _session_payload(user)

    try:
        user_id = db.insert_user(
            username=email,
            password_hash=_NO_LOCAL_PASSWORD,
            role="admin",
            email=email,
            college_name=_derive_college_name(email),
        )
    except sqlite3.IntegrityError:
        # Two requests raced to provision the same email - use the winner's row.
        user = db.get_user_by_email(email)
        if user and user["status"] != "PENDING":
            return _session_payload(user)
        return {"error": "Could not sign you in. Please try again."}
    user = db.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return _session_payload(user)


def warden_signup(id_token, college_name, hostel_id, floor):
    """Creates a PENDING warden row - no usable session until the college
    admin approves it."""
    decoded = verify_id_token(id_token)
    if not decoded:
        return {"error": _verify_failed_message("Could not verify your sign-in. Please try again.")}

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

    try:
        db.insert_user(
            username=email,
            password_hash=_NO_LOCAL_PASSWORD,
            role="warden",
            email=email,
            college_name=hostel["college_name"],
            hostel_id=hostel_id,
            floor=floor,
            status="PENDING",
        )
    except sqlite3.IntegrityError:
        return {"error": "An account with that email already exists"}
    return {"status": "pending"}


def get_session_from_token(id_token):
    """Used by @require_auth on every protected request."""
    decoded = verify_id_token(id_token)
    if not decoded:
        return None
    email = (decoded.get("email") or "").strip().lower()
    if not email:
        return None
    user = db.get_user_by_email(email)
    # A warden whose signup hasn't been approved yet has a valid Firebase
    # login but must not be able to call the API with it.
    if user and user.get("status") == "PENDING":
        return None
    return user


def create_warden(college_name, hostel_id, email, password, floor=None):
    """College admin action: creates a warden login (Firebase account + local row)."""
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
    """Admin action: removes a user's local row and their Firebase account."""
    email = user.get("email")
    if email:
        try:
            _ensure_firebase_app()
            fb_user = firebase_auth.get_user_by_email(email)
            firebase_auth.delete_user(fb_user.uid)
        except firebase_auth.UserNotFoundError:
            pass
        except Exception:
            pass
    db.delete_user(user["user_id"])


def seed_default_users():
    """No-op under Firebase auth."""
    return