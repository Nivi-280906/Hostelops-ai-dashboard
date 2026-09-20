/* ==========================================================
   Firebase web app config
   ----------------------------------------------------------
   Get these values from: Firebase Console -> Project Settings
   -> General -> "Your apps" -> Web app (</> icon) -> SDK setup
   and config.

   This is the public client config - it's fine for this to be
   visible in browser devtools/view-source, that's how Firebase
   web apps normally work. Real security comes from:
     - the Firebase service-account key on the BACKEND (never
       exposed to the browser), and
     - every API route re-verifying the ID token server-side.
   ========================================================== */
const firebaseConfig = {
  apiKey: "AIzaSyAS4K4HjM_4k-KB-c-lVofee5BlzshCETU",
  authDomain: "hostel-management-ffcef.firebaseapp.com",
  projectId: "hostel-management-ffcef",
  storageBucket: "hostel-management-ffcef.firebasestorage.app",
  messagingSenderId: "290057796385",
  appId: "1:290057796385:web:a652ac82a37cfc42739c60",
  measurementId: "G-CN31QDTFE4",
};

firebase.initializeApp(firebaseConfig);

// Match this project's original design: a page reload always requires
// signing in again (no silently-restored session from browser storage).
firebase.auth().setPersistence(firebase.auth.Auth.Persistence.NONE);
