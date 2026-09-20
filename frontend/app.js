/* ==========================================================
   HostelOps — AI-Driven Resource Optimization Platform
   app.js
   ========================================================== */

const API_BASE = (location.protocol === "file:" || (location.port && location.port !== "5000"))
  ? "http://127.0.0.1:5000/api"
  : "/api";

const state = {
  token: null,
  username: null,
  role: null,
  collegeName: null,
  hostels: [],
  hostelId: null,
  rooms: [],
  roomId: null,
  floor: null,        // warden's own floor lock (null = manages every floor)
  viewScope: "room",  // "room" | "floor" | "hostel" - electricity/water/wifi/predictions
  viewFloor: null,    // selected floor when viewScope === "floor"
  historyDays: 60,
  forecastDays: 7,
  autoRefresh: true,
  refreshMs: 15000,
  activeSection: "overview",
  predictResource: "electricity",
  alertResource: "electricity",
  effResource: "electricity",
  maintenanceStatus: "",
  reportPeriod: "monthly",
  simOcc: null,
  simOccTouched: false,
  simElecCut: 10,
  simWaterCut: 8,
  simWifiCut: 5,
  sparkHistory: { rooms: [], elec: [], water: [], wifi: [] },
  charts: {}, // Chart.js instances keyed by canvas id
  timers: { poll: null, clock: null },
  activeModalIssue: null,
  resourceMode: { electricity: "daily", water: "daily", wifi: "daily" },
};

const HOSTEL_TYPE_LABEL = { girls: "Girls", boys: "Boys", "co-ed": "Co-ed" };

function withHostel(params = {}) {
  if (state.hostelId) params.hostel_id = state.hostelId;
  return params;
}

const RESOURCE_META = {
  electricity: { color: "#F5B942", unit: "kWh", label: "Electricity" },
  water: { color: "#38BDF8", unit: "L", label: "Water" },
  wifi: { color: "#A78BFA", unit: "GB", label: "WiFi" },
  occupancy: { color: "#34D399", unit: "students", label: "Occupancy" },
};

// Global Chart.js styling defaults
if (typeof Chart !== "undefined") {
  Chart.defaults.font.family = "'Plus Jakarta Sans', 'Inter', -apple-system, sans-serif";
  Chart.defaults.color = "#535F80";
  Chart.defaults.borderColor = "rgba(5, 23, 71, 0.1)";
  if (Chart.defaults.plugins && Chart.defaults.plugins.tooltip) {
    // Tooltip keeps a dark floating background - that's a normal overlay
    // pattern and stays readable regardless of the page's light/dark theme.
    Chart.defaults.plugins.tooltip.backgroundColor = "#051747";
    Chart.defaults.plugins.tooltip.titleColor = "#FEFEFE";
    Chart.defaults.plugins.tooltip.bodyColor = "#E7E9F0";
    Chart.defaults.plugins.tooltip.borderColor = "rgba(255, 255, 255, 0.12)";
    Chart.defaults.plugins.tooltip.borderWidth = 1;
    Chart.defaults.plugins.tooltip.cornerRadius = 8;
    Chart.defaults.plugins.tooltip.padding = 10;
  }
}

/* ---------------------------------------------------------
   Toasts
   --------------------------------------------------------- */
function toast(msg, type = "err") {
  let stack = document.querySelector(".toast-stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.className = "toast-stack";
    document.body.appendChild(stack);
  }
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  stack.appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

/* ---------------------------------------------------------
   API helper
   --------------------------------------------------------- */
async function api(path, { method = "GET", body = null, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth) {
    // Always grab a *fresh* Firebase ID token (the SDK auto-refreshes it
    // in the background), rather than a token cached once at login - ID
    // tokens are short-lived (1 hour) so a stored one would go stale.
    const fbUser = firebase.auth().currentUser;
    if (fbUser) headers["Authorization"] = `Bearer ${await fbUser.getIdToken()}`;
  }

  let res;
  try {
    res = await fetch(`${API_BASE}/${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : null,
    });
  } catch (e) {
    throw { network: true, message: "Cannot reach backend. Is `python backend_api.py` running?" };
  }

  const payload = res.status !== 204 ? await res.json().catch(() => ({})) : {};

  if (res.status === 401 && auth) {
    handleSessionExpired();
    throw { auth: true, message: payload.error || "Session expired" };
  }
  if (!res.ok) {
    throw { status: res.status, message: payload.error || `Request failed (${res.status})` };
  }
  return payload;
}

function handleSessionExpired() {
  stopPolling();
  state.token = null;
  document.getElementById("app").hidden = true;
  document.getElementById("loginScreen").hidden = false;
  document.getElementById("loginError").hidden = false;
  document.getElementById("loginError").textContent = "Session expired or invalid. Please log in again.";
}

/* ---------------------------------------------------------
   Backend health check (login screen)
   --------------------------------------------------------- */
async function checkBackendStatus() {
  const statusEl = document.getElementById("backendStatus");
  if (!statusEl) return;
  try {
    await api("health", { auth: false });
    statusEl.innerHTML = '<span class="dot dot-ok"></span> Backend connected · Live IoT & AI Operational';
  } catch (e) {
    statusEl.innerHTML = '<span class="dot dot-bad"></span> Backend unreachable — start it with <code>python backend_api.py</code>';
  }
}

// Click to auto-fill default test accounts
document.querySelectorAll(".login-demo-row").forEach(row => {
  row.style.cursor = "pointer";
  row.title = "Click to auto-fill credentials";
  row.addEventListener("click", () => {
    const codeTags = row.querySelectorAll("code");
    if (codeTags.length >= 2) {
      document.getElementById("loginUsername").value = codeTags[0].textContent.trim();
      document.getElementById("loginPassword").value = codeTags[1].textContent.trim();
      document.getElementById("loginSubmitBtn").focus();
    }
  });
});

/* ---------------------------------------------------------
   Login / Signup tabs
   --------------------------------------------------------- */
document.getElementById("tabSignIn").addEventListener("click", () => setAuthTab("signin"));
document.getElementById("tabSignUp").addEventListener("click", () => setAuthTab("signup"));

function setAuthTab(which) {
  const isSignIn = which === "signin";
  document.getElementById("tabSignIn").classList.toggle("active", isSignIn);
  document.getElementById("tabSignUp").classList.toggle("active", !isSignIn);
  showAuthView(isSignIn ? "signin" : "signup");
  // Scroll straight to the top of the tabs/form so the person doesn't have
  // to manually scroll down to find the fields or the submit button.
  document.querySelector(".auth-tabs").scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ---------------------------------------------------------
   Auth screen view switching (sign in / sign up / forgot / reset)
   --------------------------------------------------------- */
let pendingResetToken = null;

function showAuthView(view) {
  const tabsEl = document.querySelector(".auth-tabs");
  document.getElementById("loginForm").hidden = view !== "signin";
  document.getElementById("signupForm").hidden = view !== "signup";
  document.getElementById("forgotForm").hidden = view !== "forgot";
  document.getElementById("resetForm").hidden = view !== "reset";
  if (view === "signup") {
    // Reset out of the "request sent" panel in case they're returning to
    // fill out the form again (e.g. after Back to sign in, then Sign Up again).
    // NOTE: signupError must stay excluded too - it should only become
    // visible when an actual submission error sets it, never just because
    // the signup view was opened.
    document.querySelectorAll("#signupForm > *:not(#signupPendingSuccess):not(#signupError)").forEach(el => el.hidden = false);
    document.getElementById("signupPendingSuccess").hidden = true;
    document.getElementById("signupError").hidden = true;
    document.getElementById("adminOnlyFields").hidden = document.querySelector("#signupRoleRow button.active").dataset.role !== "admin";
    document.getElementById("wardenOnlyFields").hidden = document.querySelector("#signupRoleRow button.active").dataset.role !== "warden";
    document.getElementById("wardenPendingNote").hidden = document.querySelector("#signupRoleRow button.active").dataset.role !== "warden";
  }
  const demoEl = document.getElementById("loginDemo");
  if (demoEl) demoEl.hidden = view !== "signin";
  if (tabsEl) tabsEl.hidden = (view === "forgot" || view === "reset");
}

document.getElementById("forgotPasswordLink").addEventListener("click", () => {
  document.getElementById("forgotError").hidden = true;
  document.getElementById("forgotSuccess").hidden = true;
  document.getElementById("forgotSubmitBtn").hidden = false;
  document.getElementById("forgotEmail").value = document.getElementById("loginUsername").value.trim();
  showAuthView("forgot");
});

document.getElementById("backToSignInFromForgot").addEventListener("click", () => {
  document.getElementById("tabSignIn").classList.add("active");
  document.getElementById("tabSignUp").classList.remove("active");
  showAuthView("signin");
});

document.getElementById("backToSignInFromReset").addEventListener("click", () => {
  document.getElementById("tabSignIn").classList.add("active");
  document.getElementById("tabSignUp").classList.remove("active");
  showAuthView("signin");
});

document.getElementById("signupPendingBackBtn").addEventListener("click", () => {
  setAuthTab("signin");
});

document.getElementById("forgotForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = document.getElementById("forgotEmail").value.trim();
  const errEl = document.getElementById("forgotError");
  const successEl = document.getElementById("forgotSuccess");
  const successBody = document.getElementById("forgotSuccessBody");
  const continueBtn = document.getElementById("forgotContinueBtn");
  const submitBtn = document.getElementById("forgotSubmitBtn");
  errEl.hidden = true;
  successEl.hidden = true;
  submitBtn.disabled = true;
  submitBtn.querySelector("span").textContent = "Sending…";

  try {
    // Firebase sends the actual reset email itself (with its own hosted
    // reset-password page) - the backend is no longer involved, and there's
    // no in-app "set new password" step anymore.
    await firebase.auth().sendPasswordResetEmail(email);
    pendingResetToken = null;
    successBody.textContent = "If an account exists for that email, a password reset link has just been sent to it. Check your inbox.";
    continueBtn.hidden = true;
    successEl.hidden = false;
    submitBtn.hidden = true;
  } catch (err) {
    // Don't reveal whether the email is registered (same behavior as before).
    if (err && err.code === "auth/invalid-email") {
      errEl.textContent = "That doesn't look like a valid email address.";
      errEl.hidden = false;
    } else {
      successBody.textContent = "If an account exists for that email, a password reset link has just been sent to it. Check your inbox.";
      continueBtn.hidden = true;
      successEl.hidden = false;
      submitBtn.hidden = true;
    }
  } finally {
    submitBtn.disabled = false;
    submitBtn.querySelector("span").textContent = "Send Reset Link";
  }
});

document.getElementById("forgotContinueBtn").addEventListener("click", () => {
  showAuthView("reset");
});

document.getElementById("resetForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const newPassword = document.getElementById("resetNewPassword").value;
  const confirmPassword = document.getElementById("resetConfirmPassword").value;
  const errEl = document.getElementById("resetError");
  const submitBtn = document.getElementById("resetSubmitBtn");
  errEl.hidden = true;

  if (!pendingResetToken) {
    errEl.textContent = "Your reset link has expired. Please request a new one.";
    errEl.hidden = false;
    return;
  }
  if (newPassword !== confirmPassword) {
    errEl.textContent = "Passwords don't match.";
    errEl.hidden = false;
    return;
  }

  // Password resets are now completed entirely on Firebase's own hosted
  // reset page (reached from the link in the email), not inside this app.
  errEl.textContent = "Please use the reset link in your email instead - it opens Firebase's own secure reset page.";
  errEl.hidden = false;
});

/* ---------------------------------------------------------
   Login / Logout
   --------------------------------------------------------- */
document.getElementById("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = document.getElementById("loginUsername").value.trim();
  const password = document.getElementById("loginPassword").value;
  const errEl = document.getElementById("loginError");
  const btn = document.getElementById("loginSubmitBtn");
  errEl.hidden = true;
  btn.disabled = true;
  btn.querySelector("span").textContent = "Authenticating…";

  try {
    const cred = await firebase.auth().signInWithEmailAndPassword(username, password);
    const idToken = await cred.user.getIdToken();
    const data = await api("login", { method: "POST", body: { id_token: idToken }, auth: false });
    applyAuthSession(data);
    await routeAfterAuth();
  } catch (err) {
    errEl.textContent = firebaseErrorMessage(err);
    errEl.hidden = false;
  } finally {
    btn.disabled = false;
    btn.querySelector("span").textContent = "Sign In";
  }
});

// Translates Firebase's auth error codes into the short, friendly messages
// this login screen already shows for its own errors.
function firebaseErrorMessage(err) {
  const code = err && err.code;
  switch (code) {
    case "auth/invalid-email":
      return "That doesn't look like a valid email address.";
    case "auth/user-not-found":
    case "auth/wrong-password":
    case "auth/invalid-credential":
      return "Invalid email or password.";
    case "auth/email-already-in-use":
      return "An account with that email already exists.";
    case "auth/weak-password":
      return "Password must be at least 6 characters.";
    case "auth/too-many-requests":
      return "Too many attempts. Please wait a moment and try again.";
    case "auth/network-request-failed":
      return "Cannot reach Firebase. Check your internet connection.";
    default:
      return (err && err.message) || "Something went wrong. Please try again.";
  }
}

document.getElementById("signupForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const role = document.querySelector("#signupRoleRow button.active").dataset.role;
  const college_name = document.getElementById("signupCollege").value.trim();
  const email = document.getElementById("signupEmail").value.trim();
  const password = document.getElementById("signupPassword").value;
  const errEl = document.getElementById("signupError");
  const btn = document.getElementById("signupSubmitBtn");
  errEl.hidden = true;
  btn.disabled = true;

  if (role === "warden") {
    const hostel_id = document.getElementById("signupHostelSelect").value;
    const floor = document.getElementById("signupFloorSelect").value;
    if (!hostel_id || floor === "") {
      errEl.textContent = "Please select a hostel and floor.";
      errEl.hidden = false;
      btn.disabled = false;
      return;
    }
    btn.querySelector("span").textContent = "Requesting access…";
    try {
      const cred = await firebase.auth().createUserWithEmailAndPassword(email, password);
      const idToken = await cred.user.getIdToken();
      const data = await api("warden-signup", {
        method: "POST",
        body: { college_name, hostel_id: Number(hostel_id), floor: Number(floor), id_token: idToken },
        auth: false,
      });
      if (data.status === "pending") {
        // The Firebase account exists but our local row is PENDING, so sign
        // this browser session back out - there's nothing to log into yet.
        try { await firebase.auth().signOut(); } catch (_) { /* ignore */ }
        document.querySelectorAll("#signupForm > *:not(#signupPendingSuccess)").forEach(el => el.hidden = true);
        document.getElementById("signupPendingSuccess").hidden = false;
        document.getElementById("signupPendingSuccess").scrollIntoView({ behavior: "smooth", block: "center" });
      }
    } catch (err) {
      errEl.textContent = err && err.code === "auth/email-already-in-use"
        ? "That email is already registered here."
        : (err && err.message) || firebaseErrorMessage(err);
      errEl.hidden = false;
      errEl.scrollIntoView({ behavior: "smooth", block: "center" });
    } finally {
      btn.disabled = false;
      btn.querySelector("span").textContent = "Request warden access";
    }
    return;
  }

  btn.querySelector("span").textContent = "Creating account…";
  try {
    const hostel_name = document.getElementById("signupHostelName").value.trim();
    const hostel_type = document.getElementById("signupHostelType").value;
    const cred = await firebase.auth().createUserWithEmailAndPassword(email, password);
    const idToken = await cred.user.getIdToken();
    const data = await api("signup", {
      method: "POST",
      body: { college_name, id_token: idToken, hostel_name, hostel_type },
      auth: false,
    });
    applyAuthSession(data);
    await routeAfterAuth();
  } catch (err) {
    errEl.textContent = err && err.code === "auth/email-already-in-use"
      ? "That email is already registered to a college here. Each email can only belong to one college — use a different email, or switch to Sign In if this college already has an account."
      : firebaseErrorMessage(err);
    errEl.hidden = false;
  } finally {
    btn.disabled = false;
    btn.querySelector("span").textContent = "Create College Account";
  }
});

/* ---------------------------------------------------------
   Signup role toggle (Admin / Warden) + hostel/floor lookup
   --------------------------------------------------------- */
(function setupSignupRoleToggle() {
  const roleButtons = document.querySelectorAll("#signupRoleRow button");
  const adminFields = document.getElementById("adminOnlyFields");
  const wardenFields = document.getElementById("wardenOnlyFields");
  const pendingNote = document.getElementById("wardenPendingNote");
  const submitBtn = document.getElementById("signupSubmitBtn");
  const collegeInput = document.getElementById("signupCollege");
  const hostelSelect = document.getElementById("signupHostelSelect");
  const floorSelect = document.getElementById("signupFloorSelect");

  function applyRole(role) {
    roleButtons.forEach((b) => b.classList.toggle("active", b.dataset.role === role));
    const isWarden = role === "warden";
    adminFields.hidden = isWarden;
    wardenFields.hidden = !isWarden;
    pendingNote.hidden = !isWarden;
    submitBtn.querySelector("span").textContent = isWarden ? "Request warden access" : "Create College Account";
    // A hidden-but-required field silently blocks the whole form's submit
    // (with no visible error, since a hidden field can't be focused to show
    // the usual validation bubble) - so the required-ness has to follow
    // whichever role's fields are actually showing.
    document.getElementById("signupHostelName").required = !isWarden;
    hostelSelect.required = isWarden;
    floorSelect.required = isWarden;
    if (isWarden) loadHostelsForCollege();
  }
  roleButtons.forEach((b) => b.addEventListener("click", () => {
    applyRole(b.dataset.role);
    // Warden reveals extra fields (hostel/floor/note) that push the submit
    // button further down - scroll it into view so it's never hidden.
    submitBtn.scrollIntoView({ behavior: "smooth", block: "center" });
  }));

  async function loadHostelsForCollege() {
    const college = collegeInput.value.trim();
    hostelSelect.innerHTML = '<option value="">Select hostel…</option>';
    floorSelect.innerHTML = '<option value="">Select floor…</option>';
    if (!college) return;
    try {
      const hostels = await api(`public/hostels?college_name=${encodeURIComponent(college)}`, { auth: false });
      if (!hostels.length) {
        hostelSelect.innerHTML = '<option value="">No hostels found for this college yet</option>';
        return;
      }
      hostels.forEach((h) => {
        const opt = document.createElement("option");
        opt.value = h.hostel_id;
        opt.textContent = `${h.name} (${HOSTEL_TYPE_LABEL[h.hostel_type] || h.hostel_type})`;
        hostelSelect.appendChild(opt);
      });
    } catch (e) {
      hostelSelect.innerHTML = '<option value="">Could not load hostels</option>';
    }
  }
  collegeInput.addEventListener("blur", () => {
    if (document.querySelector("#signupRoleRow button.active").dataset.role === "warden") loadHostelsForCollege();
  });

  hostelSelect.addEventListener("change", async () => {
    floorSelect.innerHTML = '<option value="">Select floor…</option>';
    if (!hostelSelect.value) return;
    try {
      const floors = await api(`public/hostels/${hostelSelect.value}/floors`, { auth: false });
      floors.forEach((f) => {
        const opt = document.createElement("option");
        opt.value = f;
        opt.textContent = `Floor ${f}`;
        floorSelect.appendChild(opt);
      });
    } catch (e) { /* ignore */ }
  });

  // Reset to Admin whenever the signup tab is (re)opened, so it never opens
  // mid-way through a warden request from a previous visit.
  document.getElementById("tabSignUp").addEventListener("click", () => applyRole("admin"));
})();

function applyAuthSession(data) {
  // state.token is just an "am I signed in" flag now - the real credential
  // (the Firebase ID token) is fetched fresh on every request in api(),
  // never cached here, since it expires and auto-refreshes via the SDK.
  state.token = true;
  state.username = data.username;
  state.role = data.role;
  state.collegeName = data.college_name;
  state.floor = data.floor ?? null;
}

async function routeAfterAuth() {
  try {
    state.hostels = await api("hostels");
  } catch (e) {
    state.hostels = [];
  }
  if (!state.hostels.length) {
    enterOnboarding();
  } else {
    state.hostelId = state.hostels[0].hostel_id;
    enterApp();
  }
}

document.getElementById("logoutBtn").addEventListener("click", async () => {
  stopPolling();
  try { await firebase.auth().signOut(); } catch (e) { /* ignore */ }
  state.token = null;
  state.username = null;
  state.role = null;
  state.collegeName = null;
  state.hostels = [];
  state.hostelId = null;
  state.floor = null;
  document.getElementById("app").hidden = true;
  document.getElementById("onboardScreen").hidden = true;
  document.getElementById("loginScreen").hidden = false;
  document.getElementById("loginPassword").value = "";
  setAuthTab("signin");
  checkBackendStatus();
});

/* ---------------------------------------------------------
   Hostel onboarding (first-time college setup)
   --------------------------------------------------------- */
function enterOnboarding() {
  document.getElementById("loginScreen").hidden = true;
  document.getElementById("app").hidden = true;
  document.getElementById("onboardScreen").hidden = false;
  document.getElementById("onboardCollegeName").textContent = (state.collegeName || "YOUR COLLEGE").toUpperCase();
  renderOnboardHostelList();
}

function renderOnboardHostelList() {
  const list = document.getElementById("onboardHostelList");
  const continueBtn = document.getElementById("onboardContinueBtn");
  if (!state.hostels.length) {
    list.innerHTML = '<li class="empty-state">No hostels added yet. Add at least one to continue.</li>';
  } else {
    list.innerHTML = state.hostels.map(h => `
      <li class="onboard-hostel-row">
        <span class="tag tag-${h.hostel_type === "boys" ? "admin" : "staff"}">${HOSTEL_TYPE_LABEL[h.hostel_type] || h.hostel_type}</span>
        <span>${escapeHtml(h.name)}</span>
      </li>
    `).join("");
  }
  continueBtn.hidden = state.hostels.length === 0;
}

document.getElementById("addHostelForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("hostelNameInput").value.trim();
  const hostel_type = document.getElementById("hostelTypeInput").value;
  const errEl = document.getElementById("addHostelError");
  const btn = document.getElementById("addHostelBtn");
  errEl.hidden = true;
  btn.disabled = true;
  btn.querySelector("span").textContent = "Adding…";
  try {
    const hostel = await api("hostels", { method: "POST", body: { name, hostel_type } });
    state.hostels.push(hostel);
    renderOnboardHostelList();
    e.target.reset();
    document.getElementById("hostelNameInput").focus();
  } catch (err) {
    errEl.textContent = err.message || "Could not add hostel";
    errEl.hidden = false;
  } finally {
    btn.disabled = false;
    btn.querySelector("span").textContent = "+ Add Hostel";
  }
});

document.getElementById("onboardContinueBtn").addEventListener("click", () => {
  state.hostelId = state.hostels[0].hostel_id;
  document.getElementById("onboardScreen").hidden = true;
  enterApp();
});

/* ---------------------------------------------------------
   App Shell Initialization & Role Adaptations
   --------------------------------------------------------- */
function enterApp() {
  document.getElementById("loginScreen").hidden = true;
  document.getElementById("onboardScreen").hidden = true;
  document.getElementById("app").hidden = false;
  document.body.classList.toggle("role-admin", state.role === "admin");
  document.body.classList.toggle("role-warden", state.role === "warden");
  document.body.classList.toggle("role-staff", state.role === "staff");

  document.getElementById("roleTag").textContent = state.role;
  document.getElementById("roleTag").className = `tag ${state.role === "admin" ? "tag-admin" : "tag-staff"}`;
  document.getElementById("usernameLabel").textContent = state.username;

  renderHostelSelect();
  // Wardens are locked to their own hostel; hide hostel selector in toolbar
  document.getElementById("hostelSelectGroup").hidden = state.role === "warden";

  loadRooms().then(() => {
    switchSection(state.role === "staff" ? "maintenance" : "overview");
    startClock();
    startPolling();
    fetchNotifications();
  });
}

function startClock() {
  const clockEl = document.getElementById("liveClock");
  clearInterval(state.timers.clock);
  state.timers.clock = setInterval(() => {
    clockEl.textContent = new Date().toLocaleTimeString();
  }, 1000);
}

function renderHostelSelect() {
  const sel = document.getElementById("hostelSelect");
  sel.innerHTML = state.hostels.map(h =>
    `<option value="${h.hostel_id}">${HOSTEL_TYPE_LABEL[h.hostel_type] || h.hostel_type} — ${escapeHtml(h.name)}</option>`
  ).join("");
  if (state.hostelId) sel.value = state.hostelId;
}

document.getElementById("hostelSelect").addEventListener("change", (e) => {
  state.hostelId = parseInt(e.target.value, 10);
  Object.values(state.charts).forEach(c => c.destroy && c.destroy());
  state.charts = {};
  loadRooms().then(refreshActiveSection);
});

async function loadRooms() {
  try {
    state.rooms = await api(`rooms?${new URLSearchParams(withHostel()).toString()}`);
  } catch (e) {
    toast(e.message, "err");
    return;
  }
  const sel = document.getElementById("roomSelect");
  sel.innerHTML = state.rooms.map(r => `<option value="${r.room_id}">${r.room_no} (Floor ${r.floor})</option>`).join("");
  if (state.rooms.length) state.roomId = state.rooms[0].room_id;
}

document.getElementById("roomSelect").addEventListener("change", (e) => {
  state.roomId = parseInt(e.target.value, 10);
  refreshActiveSection();
});

document.getElementById("historySlider").addEventListener("input", (e) => {
  state.historyDays = parseInt(e.target.value, 10);
  document.getElementById("historyValue").textContent = `${state.historyDays}d`;
});
document.getElementById("historySlider").addEventListener("change", refreshActiveSection);

document.getElementById("horizonSlider").addEventListener("input", (e) => {
  state.forecastDays = parseInt(e.target.value, 10);
  document.getElementById("horizonValue").textContent = `${state.forecastDays}d`;
});
document.getElementById("horizonSlider").addEventListener("change", refreshActiveSection);

document.getElementById("autoRefreshToggle").addEventListener("change", (e) => {
  state.autoRefresh = e.target.checked;
  state.autoRefresh ? startPolling() : stopPolling();
});
document.getElementById("refreshInterval").addEventListener("change", (e) => {
  state.refreshMs = parseInt(e.target.value, 10);
  if (state.autoRefresh) startPolling();
});

/* ---------------------------------------------------------
   Section Navigation
   --------------------------------------------------------- */
document.querySelectorAll(".rail-btn[data-section]").forEach(btn => {
  btn.addEventListener("click", () => switchSection(btn.dataset.section));
});

// Each section only shows the toolbar controls it actually reads, so nothing
// sits there looking actionable while silently doing nothing (the original
// source of the "Room doesn't do anything here" confusion):
//   - electricity / water / wifi / predictions: read Room + History + Horizon
//   - overview: reads Room (optional per-room drill-down), not History/Horizon
//   - reports / efficiency / alerts / maintenance / simulator: hostel-wide only
//   - comparison: institution-wide, ignores Hostel/Room/History/Horizon entirely
const TOOLBAR_SCOPE = {
  electricity: { hostel: true, room: true, history: true, horizon: true, viewScope: true },
  water: { hostel: true, room: true, history: true, horizon: true, viewScope: true },
  wifi: { hostel: true, room: true, history: true, horizon: true, viewScope: true },
  predictions: { hostel: true, room: true, history: true, horizon: true, viewScope: true },
  overview: { hostel: true, room: true, history: false, horizon: false },
  comparison: { hostel: false, room: false, history: false, horizon: false },
};
const DEFAULT_TOOLBAR_SCOPE = { hostel: true, room: false, history: false, horizon: false };

function populateScopeSelect() {
  const sel = document.getElementById("scopeSelect");
  // A warden locked to one floor only ever sees that floor and its rooms -
  // a "Hostel total" option would silently collapse back to the same
  // floor server-side (see _resolve_resource_scope), so don't offer it.
  const canGoHostelWide = !(state.role === "warden" && state.floor != null);
  sel.innerHTML = `
    <option value="room">Room</option>
    <option value="floor">Floor (total)</option>
    ${canGoHostelWide ? '<option value="hostel">Hostel (total)</option>' : ""}
  `;
  sel.value = state.viewScope;
}

function populateFloorSelect() {
  const sel = document.getElementById("floorSelect");
  const floors = [...new Set(state.rooms.map(r => r.floor))].sort((a, b) => a - b);
  sel.innerHTML = floors.map(f => `<option value="${f}">Floor ${f}</option>`).join("");
  if (!floors.includes(state.viewFloor)) {
    state.viewFloor = floors.length ? floors[0] : null;
  }
  sel.value = state.viewFloor;
}

document.getElementById("scopeSelect").addEventListener("change", (e) => {
  state.viewScope = e.target.value;
  document.getElementById("roomSelectGroup").hidden = state.viewScope !== "room";
  document.getElementById("floorSelectGroup").hidden = state.viewScope !== "floor";
  if (state.viewScope === "floor") populateFloorSelect();
  refreshActiveSection();
});

document.getElementById("floorSelect").addEventListener("change", (e) => {
  state.viewFloor = parseInt(e.target.value, 10);
  refreshActiveSection();
});

function updateToolbarForSection(name) {
  const scope = TOOLBAR_SCOPE[name] || DEFAULT_TOOLBAR_SCOPE;
  document.getElementById("hostelSelectGroup").hidden = !scope.hostel || state.role === "warden";
  document.getElementById("historyGroup").hidden = !scope.history;
  document.getElementById("horizonGroup").hidden = !scope.horizon;

  document.getElementById("scopeSelectGroup").hidden = !scope.viewScope;
  if (scope.viewScope) {
    populateScopeSelect();
    document.getElementById("roomSelectGroup").hidden = state.viewScope !== "room";
    document.getElementById("floorSelectGroup").hidden = state.viewScope !== "floor";
    if (state.viewScope === "floor") populateFloorSelect();
  } else {
    document.getElementById("roomSelectGroup").hidden = !scope.room;
    document.getElementById("floorSelectGroup").hidden = true;
  }
}

// Builds the room_id / floor / hostel_id query params for whichever scope
// (Room / Floor / Hostel) is currently selected, for electricity, water,
// wifi, and predictions. Falls back to plain room scoping for sections
// that don't offer the Room/Floor/Hostel toggle.
function resourceScopeParams() {
  if (!TOOLBAR_SCOPE[state.activeSection] || !TOOLBAR_SCOPE[state.activeSection].viewScope) {
    return { room_id: state.roomId };
  }
  if (state.viewScope === "floor") return { hostel_id: state.hostelId, floor: state.viewFloor };
  if (state.viewScope === "hostel") return { hostel_id: state.hostelId };
  return { room_id: state.roomId };
}

function switchSection(name) {
  state.activeSection = name;
  document.querySelectorAll(".rail-btn[data-section]").forEach(b => b.classList.toggle("active", b.dataset.section === name));
  document.querySelectorAll(".view").forEach(v => v.hidden = v.dataset.view !== name);
  updateToolbarForSection(name);
  refreshActiveSection();
}

function refreshActiveSection() {
  const loaders = {
    overview: loadOverview,
    comparison: loadComparison,
    electricity: () => loadResourceSection("electricity"),
    water: () => loadResourceSection("water"),
    wifi: loadWifiSection,
    predictions: loadPredictions,
    alerts: loadAlerts,
    simulator: loadSimulator,
    maintenance: loadMaintenance,
    reports: loadReports,
    efficiency: loadEfficiency,
    admin: loadAdminPanel,
    profile: loadProfile,
  };
  const fn = loaders[state.activeSection];
  if (fn) fn().catch(err => toast(err.message, "err"));
}

/* ---------------------------------------------------------
   Polling & Auto-Refresh
   --------------------------------------------------------- */
function startPolling() {
  stopPolling();
  pollTick();
  state.timers.poll = setInterval(pollTick, state.refreshMs);
}
function stopPolling() {
  clearInterval(state.timers.poll);
  state.timers.poll = null;
}
async function pollTick() {
  try {
    await refreshKPIs();
    // The Admin panel is a settings/management screen, not a live feed - its
    // data barely changes minute to minute, and re-rendering it on every poll
    // was silently wiping in-progress forms (e.g. resetting the Create Warden
    // Login "Floor" dropdown back to "All Floors" mid-fill, discarding a real
    // selection without any warning). Skip the repeat auto-refresh while the
    // admin is actively on that page; navigating there still loads it fresh.
    if (state.activeSection !== "admin") {
      refreshActiveSection();
    }
    fetchNotifications();
    document.getElementById("lastUpdated").textContent = `updated ${new Date().toLocaleTimeString()}`;
  } catch (e) {
    if (!e.auth && e.status !== 403) toast(e.message || "Refresh failed", "err");
  }
}

/* ---------------------------------------------------------
   1. OVERVIEW & KPIS
   --------------------------------------------------------- */
async function refreshKPIs() {
  // Include room_id so the Overview KPIs/alerts reflect the room & floor
  // currently selected in the toolbar, not just the whole hostel.
  const params = withHostel();
  if (state.roomId) params.room_id = state.roomId;
  const data = await api(`overview/kpis?${new URLSearchParams(params).toString()}`);

  // Monitored Facilities
  document.getElementById("kpiRooms").textContent = data.total_rooms ?? "—";
  document.getElementById("kpiOccupants").textContent = data.total_occupancy ?? "—";
  document.getElementById("kpiCapacity").textContent = data.total_capacity ?? "—";
  document.getElementById("kpiOccupancyRate").textContent = `${data.occupancy_rate_pct ?? 0}% occ`;

  // Electricity Today
  setKpi("kpiElec", data.electricity.today_kwh, "elec");
  document.getElementById("kpiElecMonth").textContent = data.electricity.monthly_kwh ?? "—";
  renderDeltaBadge("elecDeltaBadge", data.electricity.delta_pct, "kWh");

  // Water Today
  setKpi("kpiWater", data.water.today_liters, "water");
  document.getElementById("kpiWaterMonth").textContent = data.water.monthly_liters ? data.water.monthly_liters.toLocaleString() : "—";
  renderDeltaBadge("waterDeltaBadge", data.water.delta_pct, "L");

  // WiFi Today
  setKpi("kpiWifi", data.wifi.today_gb, "wifi");
  document.getElementById("kpiWifiMonth").textContent = data.wifi.monthly_gb ?? "—";
  renderDeltaBadge("wifiDeltaBadge", data.wifi.delta_pct, "GB");

  // Cost
  document.getElementById("kpiMonthCost").textContent = data.cost.current_month_inr ? data.cost.current_month_inr.toLocaleString() : "—";
  document.getElementById("kpiTodayCost").textContent = data.cost.today_inr ? data.cost.today_inr.toLocaleString() : "—";
  document.getElementById("kpiPredictedCost").textContent = data.cost.predicted_next_month_inr ? data.cost.predicted_next_month_inr.toLocaleString() : "—";
  renderDeltaBadge("costDeltaBadge", data.cost.delta_pct, "%");
  document.getElementById("kpiCostDiff").textContent = `${data.cost.delta_pct > 0 ? "+" : ""}${data.cost.delta_pct}% financial drift`;

  // Alerts
  document.getElementById("kpiActiveAlerts").textContent = data.alerts.total_active ?? 0;
  document.getElementById("kpiHighPriorityCount").textContent = data.alerts.high_priority ?? 0;
  document.getElementById("kpiHighRiskRooms").textContent = `${data.alerts.high_risk_rooms ?? 0} high-risk`;

  // Scores
  document.getElementById("kpiEffScore").textContent = data.efficiency.score ?? "--";
  document.getElementById("kpiEffRating").textContent = data.efficiency.rating ?? "Good";
  document.getElementById("kpiGreenScore").textContent = data.sustainability.score ?? "--";
  document.getElementById("kpiGreenTrend").textContent = data.sustainability.trend ?? "Stable";
  document.getElementById("kpiCo2Today").textContent = data.sustainability.co2_today_kg ?? "--";

  return data;
}

function renderDeltaBadge(elId, val, unit) {
  const el = document.getElementById(elId);
  if (!el) return;
  const num = parseFloat(val) || 0;
  el.textContent = `${num > 0 ? "↑ " : num < 0 ? "↓ " : ""}${Math.abs(num)}%`;
  el.className = `kpi-badge ${num > 5 ? "negative" : ""}`;
}

function setKpi(elId, value, sparkKey) {
  const el = document.getElementById(elId);
  if (!el) return;
  const prev = el.textContent;
  const next = value === undefined || value === null ? "—" : String(value);
  el.textContent = next;
  if (prev !== "—" && prev !== next) {
    el.classList.remove("flash");
    void el.offsetWidth;
    el.classList.add("flash");
  }
  const hist = state.sparkHistory[sparkKey];
  if (hist) {
    hist.push(typeof value === "number" ? value : 0);
    if (hist.length > 20) hist.shift();
    drawSpark(`spark${sparkKey.charAt(0).toUpperCase() + sparkKey.slice(1)}`, hist);
  }
}

function drawSpark(canvasId, data) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || data.length < 2) return;
  const key = `spark_${canvasId}`;
  const sparkColors = { sparkElec: "#F5B942", sparkWater: "#38BDF8", sparkWifi: "#A78BFA" };
  const color = sparkColors[canvasId] || "#8C97B3";
  if (state.charts[key]) {
    state.charts[key].data.labels = data.map((_, i) => i);
    state.charts[key].data.datasets[0].data = data;
    state.charts[key].update("none");
    return;
  }
  state.charts[key] = new Chart(canvas.getContext("2d"), {
    type: "line",
    data: {
      labels: data.map((_, i) => i),
      datasets: [{ data, borderColor: color, borderWidth: 1.5, pointRadius: 0, tension: 0.35, fill: false }],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      scales: { x: { display: false }, y: { display: false } },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  });
}

async function loadOverview() {
  await refreshKPIs();
}

/* ---------------------------------------------------------
   2. HOSTEL COMPARISON
   --------------------------------------------------------- */
async function loadComparison() {
  const data = await api("hostels/comparison");
  const insightEl = document.getElementById("comparisonInsightText");
  insightEl.textContent = data.ai_benchmark_insight || "Hostel operations benchmarking active.";

  const tbody = document.querySelector("#comparisonTable tbody");
  if (!data.hostels || !data.hostels.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="empty-state">No hostels configured yet.</td></tr>';
    return;
  }

  tbody.innerHTML = data.hostels.map(h => `
    <tr>
      <td><strong>${escapeHtml(h.name)}</strong></td>
      <td><span class="tag tag-${h.hostel_type === "boys" ? "admin" : "staff"}">${HOSTEL_TYPE_LABEL[h.hostel_type] || h.hostel_type}</span></td>
      <td class="mono">${h.rooms_count}</td>
      <td class="mono">${h.occupancy} / ${h.capacity}</td>
      <td class="mono">${h.electricity_kwh.toLocaleString()} kWh</td>
      <td class="mono">${h.water_liters.toLocaleString()} L</td>
      <td class="mono">${h.wifi_gb.toLocaleString()} GB</td>
      <td class="mono">₹${h.monthly_cost_inr.toLocaleString()}</td>
      <td class="mono">${h.elec_per_student} kWh/occ</td>
      <td><span class="grade-badge ${h.efficiency_score >= 80 ? "grade-a" : h.efficiency_score >= 65 ? "grade-c" : "grade-f"}">${h.efficiency_score}/100</span></td>
      <td><span class="severity-badge ${h.active_anomalies_count > 0 ? "severity-high" : "severity-low"}">${h.active_anomalies_count}</span></td>
    </tr>
  `).join("");

  // Grouped Comparison Bar Chart
  const canvas = document.getElementById("chartComparison");
  const chartData = {
    labels: data.hostels.map(h => h.name),
    datasets: [
      { label: "Electricity per Occupant (kWh)", data: data.hostels.map(h => h.elec_per_student), backgroundColor: "#F5B942", borderRadius: 4 },
      { label: "Water per Occupant (10 L)", data: data.hostels.map(h => h.water_per_student / 10), backgroundColor: "#38BDF8", borderRadius: 4 },
      { label: "Wi-Fi per Occupant (GB)", data: data.hostels.map(h => h.wifi_per_student), backgroundColor: "#A78BFA", borderRadius: 4 },
    ],
  };

  if (state.charts["chartComparison"]) {
    state.charts["chartComparison"].data = chartData;
    state.charts["chartComparison"].update();
  } else {
    state.charts["chartComparison"] = new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: chartData,
      options: chartBaseOptions(),
    });
  }
}

/* ---------------------------------------------------------
   3 & 4. ELECTRICITY & WATER MONITORING
   --------------------------------------------------------- */
// Builds the "Room 103 — Floor 1 (Capacity: 3)" / "Floor 1 — All Rooms
// (Total)" / "Girls — Narmadha — All Floors (Total)" label shown above the
// Electricity/Water charts, depending on the current Room/Floor/Hostel view.
function scopeLabel(room) {
  const hostel = state.hostels.find(h => h.hostel_id === state.hostelId);
  if (state.viewScope === "floor") {
    return `Floor ${state.viewFloor} — All Rooms${hostel ? ` (${hostel.name})` : ""} (Total)`;
  }
  if (state.viewScope === "hostel") {
    return hostel ? `${hostel.name} — All Floors (Total)` : "All Floors (Total)";
  }
  return room ? `Room ${room.room_no} — Floor ${room.floor} (Capacity: ${room.capacity})` : "";
}

async function loadResourceSection(resource) {
  const mode = state.resourceMode && state.resourceMode[resource];
  const isDiurnal = mode === "diurnal";
  const isWeekly = mode === "weekly";
  const meta = RESOURCE_META[resource];
  const room = state.rooms.find(r => r.room_id === state.roomId);

  const labelEl = document.getElementById(resource === "electricity" ? "elecRoomLabel" : "waterRoomLabel");
  if (labelEl) labelEl.textContent = scopeLabel(room);

  const diurnalInfoEl = document.getElementById(resource === "electricity" ? "elecDiurnalInfo" : "waterDiurnalInfo");
  if (diurnalInfoEl) diurnalInfoEl.hidden = !isDiurnal;

  if (isWeekly) {
    // Real hour-by-hour readings across the last 7 stored days, so you can
    // actually see *when* during the day usage happens across a whole week
    // (not just a single day like "24h Academic Routine", and not just daily
    // totals like "Daily Trend"). Backed by real stored hourly data.
    const data = await api(`diurnal/hourly_range?room_id=${state.roomId}&days=7`);
    const points = data.points || [];
    const labels = points.map(p => p.time_label);
    const values = points.map(p => (resource === "electricity" ? p.kwh : p.liters));

    const peakVal = values.length ? Math.max(...values) : 0;
    const peakIdx = values.indexOf(peakVal);
    const peakTime = peakIdx >= 0 ? points[peakIdx].time_label : "--";
    const avgPerHour = values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
    const weekTotal = values.reduce((a, b) => a + b, 0);

    if (resource === "electricity") {
      document.getElementById("elecAvg").textContent = `avg ${avgPerHour.toFixed(3)} kWh/hr`;
      document.getElementById("elecPeak").textContent = `peak ${peakVal.toFixed(2)} kWh (${peakTime})`;
      document.getElementById("elecPerStudent").textContent = `7-day total: ${weekTotal.toFixed(1)} kWh`;
      document.getElementById("elecEstCost").textContent = `est ₹${Math.round((weekTotal / 7) * 30 * 8.5).toLocaleString()}/mo`;
    } else {
      document.getElementById("waterAvg").textContent = `avg ${avgPerHour.toFixed(1)} L/hr`;
      document.getElementById("waterPeak").textContent = `peak ${peakVal.toFixed(1)} L (${peakTime})`;
      document.getElementById("waterPerStudent").textContent = `7-day total: ${weekTotal.toFixed(0)} L`;
      document.getElementById("waterEstCost").textContent = `est ₹${Math.round((weekTotal / 7) * 30 * 0.05).toLocaleString()}/mo`;
      const leakCard = document.getElementById("waterLeakCard");
      if (leakCard) leakCard.style.display = "none";
    }

    renderLineChart(resource === "electricity" ? "chartElec" : "chartWater", labels, [
      {
        label: `${meta.label} (Hour-by-Hour, Last 7 Days)`,
        data: values,
        borderColor: meta.color,
        backgroundColor: `${meta.color}22`,
        fill: true,
        tension: 0.25,
        pointRadius: 0,
        pointHoverRadius: 5,
      },
    ]);
    return;
  }

  if (isDiurnal) {
    // Load 24-Hour Diurnal Hourly Telemetry (Academic Timetable Aligned)
    const data = await api(`diurnal/hourly?room_id=${state.roomId}`);
    const hours = data.hours || [];
    const labels = hours.map(h => h.time_label);
    const values = hours.map(h => resource === "electricity" ? h.kwh : h.liters);

    const classHours = hours.filter(h => h.is_class_hour === 1);
    const classAvg = classHours.length
      ? (classHours.reduce((a, b) => a + (resource === "electricity" ? b.kwh : b.liters), 0) / classHours.length).toFixed(resource === "electricity" ? 3 : 1)
      : 0;
    const peakVal = values.length ? Math.max(...values) : 0;
    const peakFormatted = resource === "electricity" ? peakVal.toFixed(2) : peakVal.toFixed(1);
    const peakHour = hours.find(h => (resource === "electricity" ? h.kwh : h.liters) == peakVal);
    const peakTime = peakHour ? peakHour.time_label : "--";
    const dayTotal = values.reduce((a, b) => a + b, 0).toFixed(resource === "electricity" ? 2 : 0);

    if (resource === "electricity") {
      document.getElementById("elecAvg").textContent = `Class standby ~${classAvg} kWh`;
      document.getElementById("elecPeak").textContent = `Peak ${peakFormatted} kWh (${peakTime})`;
      document.getElementById("elecPerStudent").textContent = `Class sessions: 0 occ`;
      document.getElementById("elecEstCost").textContent = `24h sum: ${dayTotal} kWh`;
    } else {
      document.getElementById("waterAvg").textContent = `Class draw: ${classAvg} L`;
      document.getElementById("waterPeak").textContent = `Peak ${peakFormatted} L (${peakTime})`;
      document.getElementById("waterPerStudent").textContent = `Class sessions: 0 occ`;
      document.getElementById("waterEstCost").textContent = `24h sum: ${dayTotal} L`;
      const leakCard = document.getElementById("waterLeakCard");
      if (leakCard) leakCard.style.display = "none";
    }

    renderLineChart(resource === "electricity" ? "chartElec" : "chartWater", labels, [
      {
        label: `${meta.label} (24h Hourly Profile — Standby during Class Hours)`,
        data: values,
        borderColor: meta.color,
        backgroundColor: `${meta.color}22`,
        fill: true,
        tension: 0.35,
        pointRadius: 4,
        pointHoverRadius: 6,
      },
    ]);
    return;
  }

  // Daily Trend Mode
  const params = new URLSearchParams({ ...resourceScopeParams(), days: state.historyDays });
  const rows = await api(`${resource}?${params.toString()}`);
  const valueKey = resource === "electricity" ? "units_kwh" : "liters";

  const labels = rows.map(r => r.reading_date);
  const values = rows.map(r => r[valueKey]);
  const avg = values.length ? (values.reduce((a, b) => a + b, 0) / values.length).toFixed(1) : 0;
  const peak = values.length ? Math.max(...values).toFixed(1) : 0;

  if (resource === "electricity") {
    document.getElementById("elecAvg").textContent = `avg ${avg} kWh/day`;
    document.getElementById("elecPeak").textContent = `peak ${peak} kWh`;
    document.getElementById("elecPerStudent").textContent = `${(avg / (room ? room.capacity : 2)).toFixed(1)} kWh/student`;
    document.getElementById("elecEstCost").textContent = `est ₹${Math.round(avg * 30 * 8.5).toLocaleString()}/mo`;
  } else {
    document.getElementById("waterAvg").textContent = `avg ${avg} L/day`;
    document.getElementById("waterPeak").textContent = `peak ${peak} L`;
    document.getElementById("waterPerStudent").textContent = `${(avg / (room ? room.capacity : 2)).toFixed(0)} L/student`;
    document.getElementById("waterEstCost").textContent = `est ₹${Math.round(avg * 30 * 0.05).toLocaleString()}/mo`;

    // Check for abnormal continuous leakage
    const isLeaking = avg > 300;
    const leakCard = document.getElementById("waterLeakCard");
    if (leakCard) {
      leakCard.style.display = isLeaking ? "flex" : "none";
    }
  }

  renderLineChart(resource === "electricity" ? "chartElec" : "chartWater", labels, [
    { label: meta.label, data: values, borderColor: meta.color, backgroundColor: `${meta.color}22`, fill: true },
  ], rows.map(r => r.peak_hour));
}

/* ---------------------------------------------------------
   5. WI-FI MONITORING
   --------------------------------------------------------- */
async function loadWifiSection() {
  const mode = state.resourceMode && state.resourceMode.wifi;
  const isDiurnal = mode === "diurnal";
  const isWeekly = mode === "weekly";
  const diurnalInfoEl = document.getElementById("wifiDiurnalInfo");
  if (diurnalInfoEl) diurnalInfoEl.hidden = !isDiurnal;

  if (isWeekly) {
    const data = await api(`diurnal/hourly_range?room_id=${state.roomId}&days=7`);
    const points = data.points || [];
    const labels = points.map(p => p.time_label);
    const dataUsage = points.map(p => p.wifi_gb);
    const devices = points.map(p => p.devices);

    const peakWifi = dataUsage.length ? Math.max(...dataUsage).toFixed(2) : 0;
    const peakDev = devices.length ? Math.max(...devices) : 0;
    document.getElementById("wifiAvg").textContent = `peak ${peakWifi} GB/hr (7-day timing)`;
    document.getElementById("wifiDevAvg").textContent = `peak ${peakDev} devices`;

    renderLineChart("chartWifi", labels, [
      {
        label: "WiFi (Hour-by-Hour, Last 7 Days)",
        data: dataUsage,
        borderColor: RESOURCE_META.wifi.color,
        backgroundColor: `${RESOURCE_META.wifi.color}22`,
        fill: true,
        tension: 0.25,
        pointRadius: 0,
        pointHoverRadius: 5,
      },
    ]);
    renderLineChart("chartWifiDevices", labels, [
      {
        label: "Connected Devices (Hour-by-Hour, Last 7 Days)",
        data: devices,
        borderColor: "#8C97B3",
        backgroundColor: "#8C97B322",
        fill: true,
        tension: 0.25,
        pointRadius: 0,
        pointHoverRadius: 5,
      },
    ]);
    return;
  }

  if (isDiurnal) {
    const data = await api(`diurnal/hourly?room_id=${state.roomId}`);
    const hours = data.hours || [];
    const labels = hours.map(h => h.time_label);
    const dataUsage = hours.map(h => h.wifi_gb);
    const devices = hours.map(h => h.devices);

    const peakWifi = Math.max(...dataUsage).toFixed(2);
    const peakDev = Math.max(...devices);
    document.getElementById("wifiAvg").textContent = `Peak ${peakWifi} GB/hr (Night Study)`;
    document.getElementById("wifiDevAvg").textContent = `Max ${peakDev} devices`;

    renderLineChart("chartWifi", labels, [
      {
        label: "WiFi (24h Hourly GB — Standby during Class Hours)",
        data: dataUsage,
        borderColor: RESOURCE_META.wifi.color,
        backgroundColor: `${RESOURCE_META.wifi.color}22`,
        fill: true,
        tension: 0.35,
        pointRadius: 4,
      },
    ]);
    renderLineChart("chartWifiDevices", labels, [
      {
        label: "Connected Devices (24h)",
        data: devices,
        borderColor: "#8C97B3",
        backgroundColor: "#8C97B322",
        fill: true,
        tension: 0.35,
        pointRadius: 4,
      },
    ]);
    return;
  }

  const params = new URLSearchParams({ ...resourceScopeParams(), days: state.historyDays });
  const rows = await api(`wifi?${params.toString()}`);
  const labels = rows.map(r => r.reading_date);
  const dataUsage = rows.map(r => r.data_gb);
  const devices = rows.map(r => r.connected_devices);

  const avg = dataUsage.length ? (dataUsage.reduce((a, b) => a + b, 0) / dataUsage.length).toFixed(2) : 0;
  const devAvg = devices.length ? (devices.reduce((a, b) => a + b, 0) / devices.length).toFixed(1) : 0;
  document.getElementById("wifiAvg").textContent = `avg ${avg} GB/day`;
  document.getElementById("wifiDevAvg").textContent = `avg ${devAvg} devices`;

  renderLineChart("chartWifi", labels, [
    { label: "WiFi (GB)", data: dataUsage, borderColor: RESOURCE_META.wifi.color, backgroundColor: `${RESOURCE_META.wifi.color}22`, fill: true },
  ], rows.map(r => r.peak_hour));
  renderLineChart("chartWifiDevices", labels, [
    { label: "Connected Devices", data: devices, borderColor: "#8C97B3", backgroundColor: "#8C97B322", fill: true },
  ], rows.map(r => r.peak_hour));
}

// Academic Timetable vs Daily Trend switchers
["elecModeTabs", "waterModeTabs", "wifiModeTabs"].forEach(tabId => {
  const container = document.getElementById(tabId);
  if (!container) return;
  container.querySelectorAll(".seg-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      container.querySelectorAll(".seg-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const mode = btn.dataset.mode;
      if (tabId === "elecModeTabs") {
        state.resourceMode.electricity = mode;
        loadResourceSection("electricity");
      } else if (tabId === "waterModeTabs") {
        state.resourceMode.water = mode;
        loadResourceSection("water");
      } else if (tabId === "wifiModeTabs") {
        state.resourceMode.wifi = mode;
        loadWifiSection();
      }
    });
  });
});

/* ---------------------------------------------------------
   6. AI PREDICTIONS & OCCUPANCY FORECAST
   --------------------------------------------------------- */
document.querySelectorAll("#predictResourceTabs .seg-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    setActiveSeg("predictResourceTabs", btn);
    state.predictResource = btn.dataset.resource;
    loadPredictions().catch(err => toast(err.message, "err"));
  });
});

async function loadPredictions() {
  const resource = state.predictResource;
  const meta = RESOURCE_META[resource] || RESOURCE_META.electricity;

  if (resource === "occupancy") {
    // Occupancy forecast
    const occParams = new URLSearchParams({ ...resourceScopeParams(), n_days: state.forecastDays });
    const occRaw = await api(`predict/occupancy?${occParams.toString()}`);
    // Room-level scope returns a flat array of daily predictions; floor/hostel
    // scope returns { dates, total_capacity, daily_forecast, ... } instead -
    // normalize both into the same flat-array shape the rest of this
    // function (and the table below) expects.
    const occData = (Array.isArray(occRaw) ? occRaw : (occRaw.daily_forecast || [])).map(d => ({
      ...d,
      capacity: d.capacity ?? d.total_capacity,
    }));
    const labels = occData.map(d => d.date);
    const preds = occData.map(d => d.predicted_occupancy);

    renderLineChart("chartPredict", labels, [
      { label: "Predicted Occupants", data: preds, borderColor: meta.color, backgroundColor: `${meta.color}22`, fill: true },
    ]);

    document.getElementById("xaiExplanationText").textContent =
      "Occupancy model estimates room load based on historical weekday patterns, exam calendars, and student check-in trends. Used as the leading driver for resource forecasting.";
    document.getElementById("xaiTrendBadge").textContent = "Driver: Academic Calendar";

    // Compute the weekend vacation rate from the actual forecast returned,
    // instead of a fixed "-25%" placeholder that didn't reflect this room's
    // real weekday-vs-weekend pattern (or lack of one).
    const isWeekendDate = (dateStr) => [0, 6].includes(new Date(`${dateStr}T00:00:00`).getDay());
    const weekdayVals = occData.filter(d => !isWeekendDate(d.date)).map(d => d.predicted_occupancy);
    const weekendVals = occData.filter(d => isWeekendDate(d.date)).map(d => d.predicted_occupancy);
    const avg = (arr) => arr.reduce((a, b) => a + b, 0) / arr.length;
    let vacationRateText = "No weekend in range";
    if (weekdayVals.length && weekendVals.length) {
      const wdAvg = avg(weekdayVals);
      const weAvg = avg(weekendVals);
      const pct = wdAvg > 0 ? ((weAvg - wdAvg) / wdAvg) * 100 : 0;
      vacationRateText = `${pct >= 0 ? "+" : ""}${pct.toFixed(0)}%`;
    }

    document.getElementById("xaiFactorsGrid").innerHTML = `
      <div class="xai-factor-item"><span class="xai-factor-name">Capacity Utilization</span><span class="xai-factor-val">${occData[0] ? occData[0].occupancy_rate_pct : 75}%</span></div>
      <div class="xai-factor-item"><span class="xai-factor-name">Weekend Vacation Rate</span><span class="xai-factor-val">${vacationRateText}</span></div>
      <div class="xai-factor-item"><span class="xai-factor-name">Peak Expected Load</span><span class="xai-factor-val">Normal</span></div>
    `;

    document.querySelector("#predictTable tbody").innerHTML = occData.map(f => `
      <tr>
        <td>${f.date}</td>
        <td class="mono">${f.predicted_occupancy} students</td>
        <td class="mono">${f.capacity} max cap</td>
        <td><span class="tag tag-admin">${f.occupancy_rate_pct}% load</span></td>
        <td class="mono">—</td>
      </tr>
    `).join("");
    return;
  }

  // Resource forecasting (electricity, water, wifi)
  const valueKey = resource === "electricity" ? "units_kwh" : resource === "water" ? "liters" : "data_gb";
  const scopeParams = new URLSearchParams(resourceScopeParams());
  const [forecast, hist] = await Promise.all([
    api(`predict/${resource}?${scopeParams.toString()}&n_days=${state.forecastDays}`),
    api(`${resource}?${scopeParams.toString()}&days=30`),
  ]);

  if (!hist.length || !forecast.length) {
    renderLineChart("chartPredict", [], []);
    document.querySelector("#predictTable tbody").innerHTML = '<tr><td colspan="4" class="empty-state">Insufficient readings to train model.</td></tr>';
    return;
  }

  const histLabels = hist.map(r => r.reading_date);
  const histValues = hist.map(r => r[valueKey]);
  const fcLabels = forecast.map(f => f.date);
  const fcValues = forecast.map(f => f.predicted_value);

  const labels = [...histLabels, ...fcLabels];
  const actualData = [...histValues, ...Array(fcLabels.length).fill(null)];
  const predictedData = [...Array(histLabels.length - 1).fill(null), histValues[histValues.length - 1], ...fcValues];
  // hist rows carry the actual peak_hour for that day; forecast rows carry
  // typical_peak_hour (the historically-highest hour for that weekday,
  // since a future exact hour can't be known) - "Around" reads honestly
  // for both without overclaiming precision on the forecast half.
  const peakHours = [...hist.map(r => r.peak_hour), ...forecast.map(f => f.typical_peak_hour)];

  renderLineChart("chartPredict", labels, [
    { label: "Actual Historical", data: actualData, borderColor: meta.color, backgroundColor: `${meta.color}22`, fill: false },
    { label: "AI Forecast", data: predictedData, borderColor: "#F87171", borderDash: [6, 4], backgroundColor: "transparent", fill: false },
  ], peakHours, "Around");

  // Compare the forecast average against the *recent trailing average* (last
  // 7 actual days), not a single last data point — one noisy/peak day right
  // before the forecast start was skewing the whole trend label (e.g. showing
  // "DOWN 18.9%" off a spike day, even though the forecast tracks the normal
  // baseline for most of the horizon).
  const recentWindow = histValues.slice(-7);
  const recentAvg = recentWindow.reduce((a, b) => a + b, 0) / recentWindow.length || 1;
  const avgPred = fcValues.reduce((a, b) => a + b, 0) / fcValues.length;
  const deltaPct = (((avgPred - recentAvg) / recentAvg) * 100).toFixed(1);

  // Update XAI attribution
  document.getElementById("xaiTrendBadge").textContent = `Trend: ${deltaPct > 0 ? "UP" : "DOWN"} (${deltaPct > 0 ? "+" : ""}${deltaPct}%)`;
  document.getElementById("xaiExplanationText").textContent =
    `Forecasted ${Math.abs(deltaPct)}% ${deltaPct > 0 ? "increase" : "decrease"} in ${resource}. ` +
    `Primary drivers: Occupancy demand correlation, 7-day rolling baseline drift, and weekday/weekend patterns.`;

  document.getElementById("xaiFactorsGrid").innerHTML = `
    <div class="xai-factor-item"><span class="xai-factor-name">Occupancy Influence</span><span class="xai-factor-val">+${(Math.abs(deltaPct) * 0.55).toFixed(1)}%</span></div>
    <div class="xai-factor-item"><span class="xai-factor-name">Weekday / Weekend Drift</span><span class="xai-factor-val">+2.4%</span></div>
    <div class="xai-factor-item"><span class="xai-factor-name">Rolling 7d Momentum</span><span class="xai-factor-val">+${(Math.abs(deltaPct) * 0.3).toFixed(1)}%</span></div>
  `;

  document.querySelector("#predictTable tbody").innerHTML = forecast.map(f => `
    <tr>
      <td>${f.date}</td>
      <td class="mono">${f.predicted_value} ${meta.unit}</td>
      <td class="mono">${f.predicted_occupancy ?? "—"} students</td>
      <td><span class="kpi-badge ${deltaPct > 0 ? "negative" : ""}">${deltaPct > 0 ? "+Expected" : "Stable"}</span></td>
      <td class="mono">${formatHourLabel(f.typical_peak_hour) ?? "—"}</td>
    </tr>
  `).join("");
}

/* ---------------------------------------------------------
   7. ANOMALIES & EXPLAINABLE AI (XAI)
   --------------------------------------------------------- */
document.querySelectorAll("#alertResourceTabs .seg-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    setActiveSeg("alertResourceTabs", btn);
    state.alertResource = btn.dataset.resource;
    loadAlerts().catch(err => toast(err.message, "err"));
  });
});

document.getElementById("runScanBtn").addEventListener("click", runAnomalyScan);

async function runAnomalyScan() {
  const resultEl = document.getElementById("scanResult");
  resultEl.innerHTML = "Running Isolation Forest anomaly scan…";
  try {
    const scan = await api("alerts/scan", { method: "POST" });
    resultEl.innerHTML = `<span class="msg-ok">AI scan complete: flagged ${scan.anomalies_detected} abnormal readings. Active monitoring refreshed.</span>`;
    toast("AI anomaly scan completed successfully.", "ok");
    loadAlerts();
    refreshKPIs();
  } catch (e) {
    resultEl.innerHTML = `<span class="msg-err">${e.message}</span>`;
  }
}

async function loadAlerts() {
  const rows = await api(`alerts?${new URLSearchParams({ resource_type: state.alertResource, limit: 50, ...withHostel() }).toString()}`);
  const cardsContainer = document.getElementById("detailedAnomaliesList");
  const tbody = document.querySelector("#alertsTable tbody");

  if (!rows.length) {
    cardsContainer.innerHTML = '<div class="empty-state">No anomalies flagged for this resource. All readings conform to baseline.</div>';
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No alerts recorded yet.</td></tr>';
    return;
  }

  // Render top detailed anomaly cards with XAI explanations
  cardsContainer.innerHTML = rows.slice(0, 6).map(a => `
    <div class="anomaly-card sev-${a.severity}">
      <div class="anomaly-card-head">
        <span class="anomaly-room">Room ${a.room_no} (${a.hostel_name || "Hostel"})</span>
        <span class="severity-badge severity-${a.severity}">${a.severity}</span>
      </div>
      <div class="anomaly-stat">${escapeHtml(a.message)}</div>
      <div class="anomaly-when">${a.reading_date}${formatHourLabel(a.peak_hour) ? ` · around ${formatHourLabel(a.peak_hour)}` : ""}</div>
      
      <div class="anomaly-section-label">Probable Diagnostic Causes</div>
      <ul class="anomaly-bullets">
        ${(a.possible_causes ? a.possible_causes.split(" | ") : ["Abnormal draw pattern detected"]).map(c => `<li>${escapeHtml(c)}</li>`).join("")}
      </ul>

      <div class="anomaly-section-label">Recommended Action</div>
      <div style="font-size:12.5px; color:var(--ink);">${escapeHtml(a.recommended_action || "Inspect room fixtures")}</div>

      <div class="anomaly-card-footer">
        <button type="button" class="btn btn-primary btn-sm" onclick="openCreateTicketForRoom('${a.room_id}', '${a.resource_type}', '${escapeHtml(a.message)}')">
          Create Maintenance Work Order →
        </button>
      </div>
    </div>
  `).join("");

  // Table history
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td class="mono">${r.room_no}</td>
      <td>${r.reading_date}${formatHourLabel(r.peak_hour) ? ` <span class="mono" style="color:var(--mist-dim);">${formatHourLabel(r.peak_hour)}</span>` : ""}</td>
      <td>${escapeHtml(r.message)}</td>
      <td><span class="severity-badge severity-${r.severity}">${r.severity}</span></td>
      <td style="max-width:250px; font-size:12px;">${escapeHtml(r.possible_causes || "—")}</td>
      <td>
        <button type="button" class="btn btn-secondary btn-sm" onclick="openCreateTicketForRoom('${r.room_id}', '${r.resource_type}', '${escapeHtml(r.message)}')">
          Work Order
        </button>
      </td>
    </tr>
  `).join("");
}

/* ---------------------------------------------------------
   8. WHAT-IF OPTIMIZATION SIMULATOR
   --------------------------------------------------------- */
function initSimulatorControls() {
  const occSlider = document.getElementById("simOccSlider");
  const elecSlider = document.getElementById("simElecSlider");
  const waterSlider = document.getElementById("simWaterSlider");
  const wifiSlider = document.getElementById("simWifiSlider");

  occSlider.addEventListener("input", (e) => {
    state.simOcc = parseInt(e.target.value, 10);
    state.simOccTouched = true;
    document.getElementById("simOccDisplay").textContent = `${state.simOcc} occupants`;
  });
  elecSlider.addEventListener("input", (e) => {
    state.simElecCut = parseInt(e.target.value, 10);
    document.getElementById("simElecDisplay").textContent = `${state.simElecCut}% cut`;
  });
  waterSlider.addEventListener("input", (e) => {
    state.simWaterCut = parseInt(e.target.value, 10);
    document.getElementById("simWaterDisplay").textContent = `${state.simWaterCut}% cut`;
  });
  wifiSlider.addEventListener("input", (e) => {
    state.simWifiCut = parseInt(e.target.value, 10);
    document.getElementById("simWifiDisplay").textContent = `${state.simWifiCut}% cut`;
  });

  document.getElementById("applySimBtn").addEventListener("click", loadSimulator);
}

async function loadSimulator() {
  const params = new URLSearchParams({
    elec_reduction_pct: state.simElecCut,
    water_reduction_pct: state.simWaterCut,
    wifi_reduction_pct: state.simWifiCut,
    ...withHostel(),
  });
  // Only send an explicit occupancy once the user has actually moved that
  // slider — otherwise we'd always simulate against a fixed default (e.g. 100),
  // which for a small hostel could be 3-4x its real occupancy and swamp the
  // conservation targets with a fake demand spike. Until touched, the backend
  // uses the hostel's real current occupancy, i.e. "no occupancy change".
  if (state.simOccTouched && state.simOcc != null) {
    params.set("occupancy", state.simOcc);
  }

  const res = await api(`simulator/simulate?${params.toString()}`);

  // Anchor the slider to the real current occupancy the first time we get
  // a result, instead of leaving it at an arbitrary hardcoded default.
  if (!state.simOccTouched) {
    state.simOcc = res.baseline.occupancy;
    const occSlider = document.getElementById("simOccSlider");
    occSlider.max = Math.max(occSlider.max, res.baseline.occupancy * 3);
    occSlider.value = res.baseline.occupancy;
    document.getElementById("simOccDisplay").textContent = `${res.baseline.occupancy} occupants (current)`;
  }

  const setImpact = (wrapId, spanId, value, { invert = false } = {}) => {
    document.getElementById(spanId).textContent = value.toLocaleString();
    const isGood = invert ? value <= 0 : value >= 0;
    document.getElementById(wrapId).className = `impact-val ${isGood ? "positive" : "negative"}`;
  };

  // Savings/reduction figures: positive = good (green). A negative "savings"
  // means the scenario actually costs more, so it must render red, not green.
  setImpact("simMonthlySavingsWrap", "simMonthlySavings", res.impact.monthly_savings_inr);
  setImpact("simAnnualSavingsWrap", "simAnnualSavings", res.impact.annual_savings_inr);
  setImpact("simCo2AvoidedWrap", "simCo2Avoided", res.impact.monthly_co2_avoided_kg);
  setImpact("simBillReductionWrap", "simCostDeltaPct", res.impact.bill_reduction_pct);

  const deltaCell = (base, sim, decimals = 1) => {
    const delta = sim - base;
    // For consumption, less is better: a negative delta (usage went down) is green,
    // a positive delta (usage went up despite the targets) is red.
    const cls = delta <= 0 ? "positive" : "negative";
    const sign = delta > 0 ? "+" : "";
    return `<span class="mono ${cls}">${sign}${delta.toFixed(decimals)}</span>`;
  };

  const tbody = document.getElementById("simComparisonTableBody");
  tbody.innerHTML = `
    <tr>
      <td>Electricity (kWh/mo)</td>
      <td class="mono">${res.baseline.electricity_kwh.toLocaleString()}</td>
      <td class="mono">${res.simulated.electricity_kwh.toLocaleString()}</td>
      <td>${deltaCell(res.baseline.electricity_kwh, res.simulated.electricity_kwh)}</td>
    </tr>
    <tr>
      <td>Water (Liters/mo)</td>
      <td class="mono">${res.baseline.water_liters.toLocaleString()}</td>
      <td class="mono">${res.simulated.water_liters.toLocaleString()}</td>
      <td>${deltaCell(res.baseline.water_liters, res.simulated.water_liters, 0)}</td>
    </tr>
    <tr>
      <td>WiFi (GB/mo)</td>
      <td class="mono">${res.baseline.wifi_gb.toLocaleString()}</td>
      <td class="mono">${res.simulated.wifi_gb.toLocaleString()}</td>
      <td>${deltaCell(res.baseline.wifi_gb, res.simulated.wifi_gb)}</td>
    </tr>
    <tr>
      <td><strong>Total Utility Bill (₹)</strong></td>
      <td class="mono"><strong>₹${res.baseline.total_cost.toLocaleString()}</strong></td>
      <td class="mono"><strong>₹${res.simulated.total_cost.toLocaleString()}</strong></td>
      <td>${deltaCell(res.baseline.total_cost, res.simulated.total_cost, 2).replace(/^(<span class="mono \w+">)/, "$1₹")}</td>
    </tr>
  `;

  // Simulator Comparison Chart
  const canvas = document.getElementById("chartSimulator");
  const chartData = {
    labels: ["Electricity Cost (₹)", "Water Cost (₹)", "WiFi Cost (₹)", "Total Bill (₹)"],
    datasets: [
      { label: "Current Baseline", data: [res.baseline.electricity_cost, res.baseline.water_cost, res.baseline.wifi_cost, res.baseline.total_cost], backgroundColor: "#8C97B3" },
      { label: "Optimized Scenario", data: [res.simulated.electricity_cost, res.simulated.water_cost, res.simulated.wifi_cost, res.simulated.total_cost], backgroundColor: "#34D399" },
    ],
  };

  if (state.charts["chartSimulator"]) {
    state.charts["chartSimulator"].data = chartData;
    state.charts["chartSimulator"].update();
  } else {
    state.charts["chartSimulator"] = new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: chartData,
      options: chartBaseOptions(),
    });
  }
}

/* ---------------------------------------------------------
   10. CLOSED-LOOP MAINTENANCE WORKFLOW
   --------------------------------------------------------- */
document.querySelectorAll("#maintenanceStatusTabs .seg-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    setActiveSeg("maintenanceStatusTabs", btn);
    state.maintenanceStatus = btn.dataset.status;
    loadMaintenance().catch(err => toast(err.message, "err"));
  });
});

async function loadMaintenance() {
  const params = new URLSearchParams(withHostel());
  if (state.maintenanceStatus) params.set("status", state.maintenanceStatus);

  const issues = await api(`maintenance?${params.toString()}`);
  const tbody = document.querySelector("#maintenanceTable tbody");

  if (!issues.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty-state">No work orders match this filter.</td></tr>';
    return;
  }

  tbody.innerHTML = issues.map(m => `
    <tr>
      <td class="mono">#${m.issue_id}</td>
      <td>${escapeHtml(m.hostel_name)}</td>
      <td class="mono">Room ${m.room_no}</td>
      <td><span class="tag tag-staff">${m.resource_type}</span></td>
      <td><strong>${escapeHtml(m.problem)}</strong></td>
      <td><span class="severity-badge severity-${m.severity}">${m.severity}</span></td>
      <td>${escapeHtml(m.assigned_username || "Unassigned")}</td>
      <td><span class="tag ${m.status === "RESOLVED" ? "tag-admin" : m.status === "IN_PROGRESS" ? "tag-staff" : ""}">${m.status}</span></td>
      <td class="mono" style="font-size:11px;">${m.created_at}</td>
      <td>
        <button type="button" class="btn btn-secondary btn-sm" onclick="openUpdateModal(${m.issue_id}, '${m.status}', '${escapeHtml(m.problem)}')">
          Update
        </button>
      </td>
    </tr>
  `).join("");
}

// Open New Ticket Modal
document.getElementById("openNewTicketModalBtn").addEventListener("click", () => {
  openCreateTicketModal();
});
document.getElementById("createWaterTicketQuickBtn").addEventListener("click", () => {
  openCreateTicketForRoom(state.roomId, "water", "Suspected pipeline leak or open faucet");
});

function openCreateTicketModal() {
  const hostelSel = document.getElementById("modalHostelSelect");
  hostelSel.innerHTML = state.hostels.map(h => `<option value="${h.hostel_id}">${escapeHtml(h.name)}</option>`).join("");
  if (state.hostelId) hostelSel.value = state.hostelId;

  const roomSel = document.getElementById("modalRoomSelect");
  roomSel.innerHTML = state.rooms.map(r => `<option value="${r.room_id}">${r.room_no}</option>`).join("");
  if (state.roomId) roomSel.value = state.roomId;

  document.getElementById("ticketMsg").innerHTML = "";
  document.getElementById("ticketModal").hidden = false;
}

window.openCreateTicketForRoom = function(roomId, resourceType, problemText) {
  openCreateTicketModal();
  if (roomId) document.getElementById("modalRoomSelect").value = roomId;
  if (resourceType) document.getElementById("modalResourceSelect").value = resourceType;
  if (problemText) document.getElementById("modalProblemInput").value = problemText;
};

document.getElementById("closeTicketModalBtn").addEventListener("click", () => {
  document.getElementById("ticketModal").hidden = true;
});
document.getElementById("cancelTicketModalBtn").addEventListener("click", () => {
  document.getElementById("ticketModal").hidden = true;
});

document.getElementById("ticketForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const hostel_id = parseInt(document.getElementById("modalHostelSelect").value, 10);
  const room_id = parseInt(document.getElementById("modalRoomSelect").value, 10);
  const resource_type = document.getElementById("modalResourceSelect").value;
  const problem = document.getElementById("modalProblemInput").value.trim();
  const severity = document.getElementById("modalSeveritySelect").value;
  const description = document.getElementById("modalDescInput").value.trim();

  try {
    await api("maintenance", {
      method: "POST",
      body: { hostel_id, room_id, resource_type, problem, severity, description },
    });
    toast("Work order created and logged in priority queue.", "ok");
    document.getElementById("ticketModal").hidden = true;
    e.target.reset();
    if (state.activeSection === "maintenance") loadMaintenance();
    refreshKPIs();
  } catch (err) {
    document.getElementById("ticketMsg").innerHTML = `<span class="msg-err">${err.message}</span>`;
  }
});

// Update Work Order Modal
window.openUpdateModal = function(issueId, currentStatus, problemText) {
  state.activeModalIssue = issueId;
  document.getElementById("updateTicketIdDisplay").textContent = issueId;
  document.getElementById("updateTicketDetails").textContent = `Problem: ${problemText}`;
  document.getElementById("updateStatusSelect").value = currentStatus;
  document.getElementById("updateNotesInput").value = "";
  document.getElementById("updateTicketMsg").innerHTML = "";
  document.getElementById("updateTicketModal").hidden = false;
};

document.getElementById("closeUpdateModalBtn").addEventListener("click", () => {
  document.getElementById("updateTicketModal").hidden = true;
});
document.getElementById("cancelUpdateModalBtn").addEventListener("click", () => {
  document.getElementById("updateTicketModal").hidden = true;
});

document.getElementById("updateTicketForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!state.activeModalIssue) return;
  const status = document.getElementById("updateStatusSelect").value;
  const resolution_notes = document.getElementById("updateNotesInput").value.trim();

  try {
    await api(`maintenance/${state.activeModalIssue}`, {
      method: "PATCH",
      body: { status, resolution_notes },
    });
    toast(`Work order #${state.activeModalIssue} updated to ${status}.`, "ok");
    document.getElementById("updateTicketModal").hidden = true;
    loadMaintenance();
    refreshKPIs();
  } catch (err) {
    document.getElementById("updateTicketMsg").innerHTML = `<span class="msg-err">${err.message}</span>`;
  }
});

/* ---------------------------------------------------------
   9. OPERATIONAL REPORTS (includes carbon/sustainability accounting)
   --------------------------------------------------------- */
async function loadSustainabilityStats() {
  try {
    const data = await api(`sustainability?${new URLSearchParams(withHostel()).toString()}`);
    document.getElementById("sustScore").textContent = data.sustainability_score ?? 80;
    document.getElementById("sustMonthCo2").textContent = data.total_co2_kg ? data.total_co2_kg.toLocaleString() : "0";
    document.getElementById("sustAnnualCo2").textContent = data.annual_projected_co2_tons ?? "0";
    document.getElementById("sustTrees").textContent = data.trees_offset_needed ?? "0";
    document.getElementById("sustSolarKwh").textContent = data.clean_energy_potential_kwh ? data.clean_energy_potential_kwh.toLocaleString() : "450";
  } catch (e) { /* ignore - non-critical panel */ }
}
document.querySelectorAll("#reportPeriodTabs .seg-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    setActiveSeg("reportPeriodTabs", btn);
    state.reportPeriod = btn.dataset.period;
    loadReports().catch(err => toast(err.message, "err"));
  });
});

document.getElementById("printReportBtn").addEventListener("click", () => {
  window.print();
});

async function loadReports() {
  const rep = await api(`reports/generate?period=${state.reportPeriod}&${new URLSearchParams(withHostel()).toString()}`);
  loadSustainabilityStats();

  document.getElementById("reportMetaText").textContent =
    `Institution: ${rep.college_name || "Demo College"} | Facility: ${rep.hostel_name} | Scope: ${rep.period.toUpperCase()} | Generated: ${rep.date_generated}`;

  document.getElementById("reportStatsGrid").innerHTML = `
    <div class="report-stat-box"><div class="report-stat-lbl">Rooms Audited</div><div class="report-stat-num">${rep.rooms_monitored}</div></div>
    <div class="report-stat-box"><div class="report-stat-lbl">Electricity Consumed</div><div class="report-stat-num">${rep.consumption.electricity_kwh.toLocaleString()} kWh</div></div>
    <div class="report-stat-box"><div class="report-stat-lbl">Water Consumed</div><div class="report-stat-num">${rep.consumption.water_liters.toLocaleString()} L</div></div>
    <div class="report-stat-box"><div class="report-stat-lbl">Wi-Fi Data Transferred</div><div class="report-stat-num">${rep.consumption.wifi_gb.toLocaleString()} GB</div></div>
    <div class="report-stat-box"><div class="report-stat-lbl">Efficiency Index</div><div class="report-stat-num">${rep.efficiency_score}/100</div></div>
    <div class="report-stat-box"><div class="report-stat-lbl">Carbon Footprint</div><div class="report-stat-num">${rep.co2_kg.toLocaleString()} kg CO₂</div></div>
  `;

  document.getElementById("reportFinancialsGrid").innerHTML = `
    <div class="report-stat-box"><div class="report-stat-lbl">Electricity Bill</div><div class="report-stat-num">₹${rep.financials.electricity_cost.toLocaleString()}</div></div>
    <div class="report-stat-box"><div class="report-stat-lbl">Water Charges</div><div class="report-stat-num">₹${rep.financials.water_cost.toLocaleString()}</div></div>
    <div class="report-stat-box"><div class="report-stat-lbl">Bandwidth Cost</div><div class="report-stat-num">₹${rep.financials.wifi_cost.toLocaleString()}</div></div>
    <div class="report-stat-box" style="background:#EAF3EC; border-color:#C7DBC9;"><div class="report-stat-lbl" style="color:#3E5645;">Total Utility Incurred</div><div class="report-stat-num" style="color:#3E5645;">₹${rep.financials.total_cost.toLocaleString()}</div></div>
  `;

  document.getElementById("reportRecsList").innerHTML = (rep.top_recommendations || []).map((r, i) => `
    <div class="report-item">
      <strong>${i + 1}. ${escapeHtml(r.title)}</strong> — Potential Saving: ₹${r.potential_savings_monthly.toLocaleString()}/mo.
      <br><span style="color:#8E7A99;">${escapeHtml(r.description)}</span>
    </div>
  `).join("");
}

/* ---------------------------------------------------------
   13. ROOM EFFICIENCY (ADMIN/WARDEN)
   --------------------------------------------------------- */
document.querySelectorAll("#effResourceTabs .seg-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    setActiveSeg("effResourceTabs", btn);
    state.effResource = btn.dataset.resource;
    loadEfficiency().catch(err => toast(err.message, "err"));
  });
});

async function loadEfficiency() {
  const resource = state.effResource;
  const meta = RESOURCE_META[resource];
  const rows = await api(`efficiency/${resource}?${new URLSearchParams(withHostel()).toString()}`);
  const top = rows.slice(0, 15);

  const canvas = document.getElementById("chartEfficiency");
  const chartData = {
    labels: top.map(r => r.room_no),
    datasets: [{ label: `${meta.label} per occupant`, data: top.map(r => r.per_occupant), backgroundColor: meta.color, borderRadius: 4 }],
  };

  if (state.charts["chartEfficiency"]) {
    state.charts["chartEfficiency"].data = chartData;
    state.charts["chartEfficiency"].update();
  } else {
    state.charts["chartEfficiency"] = new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: chartData,
      options: { ...chartBaseOptions(), plugins: { ...chartBaseOptions().plugins, legend: { display: false } } },
    });
  }

  // Detailed Categorization Table — uses the real per-room score/grade/reason
  // the backend now computes (previously hardcoded to 42/100 or 78/100 for
  // every room regardless of actual usage).
  const tbody = document.querySelector("#efficiencyDetailedTable tbody");
  tbody.innerHTML = top.map(r => `
      <tr>
        <td class="mono"><strong>Room ${r.room_no}</strong></td>
        <td class="mono">${r.per_occupant} ${meta.unit}/occupant</td>
        <td><span class="grade-badge ${r.badge_class}">${r.efficiency_score}/100</span></td>
        <td><span class="grade-badge ${r.badge_class}">${r.grade}</span></td>
        <td style="font-size:12.5px; color:var(--mist);">${escapeHtml(r.reason)}</td>
      </tr>
    `).join("");
}

/* ---------------------------------------------------------
   14. ADMIN PANEL & DEMO CONTROLLER
   --------------------------------------------------------- */
async function loadProfile() {
  if (state.role !== "warden") return;
  const me = await api("me");
  document.getElementById("profileEmail").textContent = me.username || "—";
  document.getElementById("profileAvatar").textContent = (me.username || "?").trim()[0] || "?";
  document.getElementById("profileCollege").textContent = me.college_name || "—";
  document.getElementById("profileHostel").textContent = me.hostel_name
    ? `${me.hostel_name} (${HOSTEL_TYPE_LABEL[me.hostel_type] || me.hostel_type})`
    : "—";
  document.getElementById("profileFloor").textContent = me.floor != null ? `Floor ${me.floor}` : "All floors";
  document.getElementById("profileCreated").textContent = me.created_at || "—";
}

document.getElementById("changePasswordForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msgEl = document.getElementById("changePasswordMsg");
  const btn = document.getElementById("changePasswordBtn");
  msgEl.innerHTML = "";

  const currentPassword = document.getElementById("currentPassword").value;
  const newPassword = document.getElementById("newPassword").value;
  const confirmNewPassword = document.getElementById("confirmNewPassword").value;

  if (newPassword !== confirmNewPassword) {
    msgEl.innerHTML = '<span class="msg-err">New passwords do not match.</span>';
    return;
  }
  if (newPassword.length < 6) {
    msgEl.innerHTML = '<span class="msg-err">New password must be at least 6 characters.</span>';
    return;
  }

  const user = firebase.auth().currentUser;
  if (!user || !user.email) {
    msgEl.innerHTML = '<span class="msg-err">Session expired. Please log in again.</span>';
    return;
  }

  btn.disabled = true;
  btn.textContent = "Updating…";
  try {
    // Firebase requires a recent sign-in for sensitive actions like a
    // password change, so re-authenticate with the current password first.
    const credential = firebase.auth.EmailAuthProvider.credential(user.email, currentPassword);
    await user.reauthenticateWithCredential(credential);
    await user.updatePassword(newPassword);
    msgEl.innerHTML = '<span class="msg-ok">Password updated successfully.</span>';
    document.getElementById("changePasswordForm").reset();
  } catch (err) {
    msgEl.innerHTML = `<span class="msg-err">${firebaseErrorMessage(err)}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Update Password";
  }
});

async function loadAdminPanel() {
  if (state.role !== "admin") return;
  await Promise.all([loadAdminHostels(), loadAdminUsers(), loadPendingWardens()]);
}

async function loadPendingWardens() {
  if (state.role !== "admin") return;
  const rows = await api("pending-wardens");
  const tbody = document.querySelector("#pendingWardensTable tbody");
  const emptyEl = document.getElementById("pendingWardensEmpty");
  emptyEl.hidden = rows.length > 0;
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td>${escapeHtml(r.email)}</td>
      <td>${escapeHtml(r.hostel_name || "—")}</td>
      <td>${r.floor}</td>
      <td class="mono">${r.created_at}</td>
      <td>
        <button type="button" class="btn btn-secondary btn-sm" data-approve="${r.user_id}">Approve</button>
        <button type="button" class="btn btn-secondary btn-sm" data-reject="${r.user_id}">Reject</button>
      </td>
    </tr>
  `).join("");
}

document.querySelector("#pendingWardensTable tbody").addEventListener("click", async (e) => {
  const approveId = e.target.dataset.approve;
  const rejectId = e.target.dataset.reject;
  if (!approveId && !rejectId) return;
  try {
    if (approveId) {
      const result = await api(`pending-wardens/${approveId}/approve`, { method: "POST" });
      if (result.email_warning) {
        toast(result.email_warning, "err");
      } else {
        toast("Warden approved — notification email sent.", "ok");
      }
    }
    if (rejectId) await api(`pending-wardens/${rejectId}/reject`, { method: "POST" });
    loadPendingWardens();
  } catch (err) {
    toast(err.message || "Could not update that request");
  }
});

async function loadAdminHostels() {
  const hostels = await api("hostels");
  state.hostels = hostels;
  renderHostelSelect();

  const wardenHostelSel = document.getElementById("wardenHostel");
  wardenHostelSel.innerHTML = hostels.map(h =>
    `<option value="${h.hostel_id}">${HOSTEL_TYPE_LABEL[h.hostel_type] || h.hostel_type} — ${escapeHtml(h.name)}</option>`
  ).join("");
  await populateWardenFloors();

  const tbody = document.querySelector("#hostelsTable tbody");
  if (!hostels.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty-state">No hostels configured yet.</td></tr>';
    return;
  }
  const roomCounts = await Promise.all(
    hostels.map(h => api(`rooms?hostel_id=${h.hostel_id}`).then(r => r.length).catch(() => "—"))
  );
  tbody.innerHTML = hostels.map((h, i) => `
    <tr>
      <td>${escapeHtml(h.name)}</td>
      <td><span class="tag ${h.hostel_type === "boys" ? "tag-admin" : "tag-staff"}">${HOSTEL_TYPE_LABEL[h.hostel_type] || h.hostel_type}</span></td>
      <td class="mono">${roomCounts[i]}</td>
      <td><button type="button" class="btn btn-danger-ghost" data-delete-hostel="${h.hostel_id}">Remove</button></td>
    </tr>
  `).join("");

  tbody.querySelectorAll("[data-delete-hostel]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = parseInt(btn.dataset.deleteHostel, 10);
      const hostel = hostels.find(h => h.hostel_id === id);
      if (!confirm(`Remove "${hostel.name}"? This deletes all its rooms and reading history.`)) return;
      try {
        await api(`hostels/${id}`, { method: "DELETE" });
        if (state.hostelId === id) state.hostelId = null;
        await loadAdminHostels();
        if (!state.hostelId && state.hostels.length) state.hostelId = state.hostels[0].hostel_id;
        renderHostelSelect();
        if (state.hostelId) loadRooms().then(refreshActiveSection);
        toast(`"${hostel.name}" removed.`, "ok");
      } catch (err) {
        toast(err.message, "err");
      }
    });
  });
}

document.getElementById("createHostelForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msgEl = document.getElementById("createHostelMsg");
  const name = document.getElementById("newHostelName").value.trim();
  const hostel_type = document.getElementById("newHostelType").value;
  msgEl.innerHTML = "";
  try {
    await api("hostels", { method: "POST", body: { name, hostel_type } });
    msgEl.innerHTML = `<span class="msg-ok">Hostel "${escapeHtml(name)}" added with demo history.</span>`;
    e.target.reset();
    await loadAdminHostels();
    if (!state.hostelId) {
      state.hostelId = state.hostels[0].hostel_id;
      renderHostelSelect();
      loadRooms().then(refreshActiveSection);
    }
  } catch (err) {
    msgEl.innerHTML = `<span class="msg-err">${escapeHtml(err.message)}</span>`;
  }
});

async function populateWardenFloors() {
  const wardenHostelSel = document.getElementById("wardenHostel");
  const wardenFloorSel = document.getElementById("wardenFloor");
  const previousValue = wardenFloorSel.value; // preserve an in-progress selection across re-renders
  wardenFloorSel.innerHTML = '<option value="">All Floors (whole hostel)</option>';
  if (!wardenHostelSel.value) return;
  try {
    const floors = await api(`public/hostels/${wardenHostelSel.value}/floors`, { auth: false });
    floors.forEach((f) => {
      const opt = document.createElement("option");
      opt.value = f;
      opt.textContent = `Floor ${f} only`;
      wardenFloorSel.appendChild(opt);
    });
    if (previousValue && floors.map(String).includes(previousValue)) {
      wardenFloorSel.value = previousValue;
    }
  } catch (e) { /* ignore - falls back to "All Floors" */ }
}
document.getElementById("wardenHostel").addEventListener("change", populateWardenFloors);

document.getElementById("createWardenForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msgEl = document.getElementById("createWardenMsg");
  const hostel_id = parseInt(document.getElementById("wardenHostel").value, 10);
  const floorVal = document.getElementById("wardenFloor").value;
  const floor = floorVal === "" ? null : parseInt(floorVal, 10);
  const email = document.getElementById("wardenEmail").value.trim();
  const password = document.getElementById("wardenPassword").value;
  msgEl.innerHTML = "";
  if (!hostel_id) {
    msgEl.innerHTML = `<span class="msg-err">Add a hostel first.</span>`;
    return;
  }
  try {
    await api("wardens", { method: "POST", body: { email, password, hostel_id, floor } });
    msgEl.innerHTML = `<span class="msg-ok">Warden login created for ${escapeHtml(email)}${floor ? ` (Floor ${floor} only)` : " (whole hostel)"}.</span>`;
    e.target.reset();
    populateWardenFloors();
    loadAdminUsers();
  } catch (err) {
    msgEl.innerHTML = `<span class="msg-err">${escapeHtml(err.message)}</span>`;
  }
});

async function loadAdminUsers() {
  if (state.role !== "admin") return;
  const users = await api("users");
  const hostelName = (id) => {
    const h = state.hostels.find(h => h.hostel_id === id);
    return h ? h.name : "—";
  };
  document.querySelector("#usersTable tbody").innerHTML = users.map(u => `
    <tr>
      <td>${escapeHtml(u.username)}</td>
      <td><span class="tag ${u.role === "admin" ? "tag-admin" : "tag-staff"}">${u.role}</span></td>
      <td>${u.hostel_id ? `${escapeHtml(hostelName(u.hostel_id))}${u.floor != null ? ` — Floor ${u.floor}` : ""}` : (u.role === "admin" ? "All Hostels" : "—")}</td>
      <td class="mono">${u.created_at}</td>
      <td>${u.username === state.username ? "" : `<button type="button" class="btn btn-danger-ghost" data-delete-user="${u.user_id}">Remove</button>`}</td>
    </tr>
  `).join("");

  document.querySelectorAll("#usersTable [data-delete-user]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = parseInt(btn.dataset.deleteUser, 10);
      const user = users.find(u => u.user_id === id);
      if (!user) return;
      if (!confirm(`Remove account "${user.username}"? This can't be undone.`)) return;
      try {
        await api(`users/${id}`, { method: "DELETE" });
        toast(`"${user.username}" removed.`, "ok");
        loadAdminUsers();
      } catch (err) {
        toast(err.message, "err");
      }
    });
  });
}

/* ---------------------------------------------------------
   15. NOTIFICATIONS SYSTEM
   --------------------------------------------------------- */
const notifBtn = document.getElementById("notifBtn");
const notifPopover = document.getElementById("notifPopover");

notifBtn.addEventListener("click", () => {
  notifPopover.hidden = !notifPopover.hidden;
});

document.addEventListener("click", (e) => {
  if (!notifBtn.contains(e.target) && !notifPopover.contains(e.target)) {
    notifPopover.hidden = true;
  }
});

document.getElementById("markAllReadBtn").addEventListener("click", async () => {
  try {
    await api("notifications/read", { method: "POST" });
    fetchNotifications();
  } catch (e) { /* ignore */ }
});

async function fetchNotifications() {
  if (!state.token) return;
  try {
    const notes = await api("notifications?unread_only=1");
    const badge = document.getElementById("notifBadge");
    if (notes.length > 0) {
      badge.textContent = notes.length;
      badge.hidden = false;
    } else {
      badge.hidden = true;
    }

    const list = document.getElementById("notifList");
    if (!notes.length) {
      list.innerHTML = '<div class="empty-state">No new notifications.</div>';
    } else {
      list.innerHTML = notes.map(n => `
        <div class="notif-item type-${n.type}">
          <div class="notif-title">${escapeHtml(n.title)}</div>
          <div class="notif-msg">${escapeHtml(n.message)}</div>
        </div>
      `).join("");
    }
  } catch (e) { /* ignore */ }
}

/* ---------------------------------------------------------
   16. AI CHATBOT ASSISTANT
   --------------------------------------------------------- */
const chatbotDrawer = document.getElementById("chatbotDrawer");
const chatbotToggleBtn = document.getElementById("chatbotToggleBtn");
const closeChatbotBtn = document.getElementById("closeChatbotBtn");
const chatbotMessages = document.getElementById("chatbotMessages");
const chatbotForm = document.getElementById("chatbotForm");
const chatbotInput = document.getElementById("chatbotInput");

chatbotToggleBtn.addEventListener("click", () => {
  chatbotDrawer.hidden = !chatbotDrawer.hidden;
  if (!chatbotDrawer.hidden) chatbotInput.focus();
});

closeChatbotBtn.addEventListener("click", () => {
  chatbotDrawer.hidden = true;
});

document.querySelectorAll("#chatbotChips .chip-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    chatbotInput.value = btn.dataset.query;
    sendChatMessage();
  });
});

chatbotForm.addEventListener("submit", (e) => {
  e.preventDefault();
  sendChatMessage();
});

async function sendChatMessage() {
  const q = chatbotInput.value.trim();
  if (!q) return;

  appendChatBubble("user", q);
  chatbotInput.value = "";

  const loadingEl = appendChatBubble("bot", "Analyzing institutional database…");

  try {
    const res = await api("chatbot/query", { method: "POST", body: { query: q } });
    loadingEl.textContent = res.response;
  } catch (e) {
    loadingEl.textContent = "I could not retrieve the requested data. Please verify network connection.";
  }
}

function appendChatBubble(sender, text) {
  const el = document.createElement("div");
  el.className = `chat-msg ${sender}`;
  el.textContent = text;
  chatbotMessages.appendChild(el);
  chatbotMessages.scrollTop = chatbotMessages.scrollHeight;
  return el;
}

/* ---------------------------------------------------------
   Chart Helpers
   --------------------------------------------------------- */
function renderLineChart(canvasId, labels, datasets, peakHours, hourLabelPrefix = "Peaked around") {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  // Adds a second tooltip line naming the hour a day's total actually
  // peaked at (or, for future forecast days, typically peaks at), when
  // that data is available - callers that don't pass peakHours are
  // unaffected (returns nothing to render).
  const afterTitle = (items) => {
    if (!peakHours || !items.length) return [];
    const hour = peakHours[items[0].dataIndex];
    const label = formatHourLabel(hour);
    return label ? [`${hourLabelPrefix} ${label}`] : [];
  };
  if (state.charts[canvasId]) {
    state.charts[canvasId].data.labels = labels;
    state.charts[canvasId].data.datasets = datasets.map(d => ({ ...baseLineOpts(), ...d }));
    state.charts[canvasId].options.plugins.tooltip.callbacks.afterTitle = afterTitle;
    state.charts[canvasId].update();
    return;
  }
  const opts = chartBaseOptions();
  opts.plugins.tooltip.callbacks.afterTitle = afterTitle;
  state.charts[canvasId] = new Chart(canvas.getContext("2d"), {
    type: "line",
    data: { labels, datasets: datasets.map(d => ({ ...baseLineOpts(), ...d })) },
    options: opts,
  });
}

function baseLineOpts() {
  return { borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.3 };
}

function isIsoDate(str) {
  return typeof str === "string" && /^\d{4}-\d{2}-\d{2}$/.test(str);
}
function formatDateShort(label) {
  if (!isIsoDate(label)) return label;
  const d = new Date(`${label}T00:00:00`);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}
function formatDateFull(label) {
  if (!isIsoDate(label)) return label;
  const d = new Date(`${label}T00:00:00`);
  return d.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}
// Turns a 0-23 hour into "9 PM" etc., for surfacing exactly when a daily
// total's usage actually peaked (or, for future forecast days, the hour
// that has historically run highest on that weekday).
function formatHourLabel(hour) {
  if (hour == null) return null;
  const period = hour < 12 ? "AM" : "PM";
  let h = hour % 12;
  if (h === 0) h = 12;
  return `${h} ${period}`;
}

function chartBaseOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    scales: {
      x: {
        grid: { color: "#E7E9F0" },
        ticks: {
          color: "#535F80", maxTicksLimit: 8, font: { family: "JetBrains Mono", size: 10.5 },
          // Readings are one-per-day totals (no time-of-day is recorded), so we
          // just render the raw "2026-08-14" as a nicer "14 Aug" instead of
          // fabricating a fake time. Non-date labels (hourly ticks, hostel
          // names, etc.) pass through unchanged.
          callback: function (value) { return formatDateShort(this.getLabelForValue(value)); },
        },
      },
      y: { grid: { color: "#E7E9F0" }, ticks: { color: "#535F80", font: { family: "JetBrains Mono", size: 10.5 } } },
    },
    plugins: {
      legend: { labels: { color: "#535F80", font: { family: "Inter", size: 12 } } },
      tooltip: {
        backgroundColor: "#101827", borderColor: "#212A44", borderWidth: 1, titleColor: "#ECF1FA", bodyColor: "#ECF1FA",
        callbacks: { title: (items) => (items.length ? formatDateFull(items[0].label) : "") },
      },
    },
  };
}

function setActiveSeg(groupId, activeBtn) {
  document.querySelectorAll(`#${groupId} .seg-btn`).forEach(b => b.classList.toggle("active", b === activeBtn));
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

/* ---------------------------------------------------------
   Boot
   --------------------------------------------------------- */
checkBackendStatus();
initSimulatorControls();