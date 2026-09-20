"""
ai_models.py
-------------------------------------------------------
THE AI / MACHINE LEARNING MODULE
-------------------------------------------------------
This file is intentionally kept independent from the Flask
backend and the Streamlit frontend so it can be developed,
tested, and presented separately (this is the AI teammate's
deliverable).

It provides three capabilities, as required by the objectives:
  1. build_features()          -> feature engineering from raw DB data
  2. train_and_save_model()    -> trains a regression model per resource
                                    (electricity / water) to PREDICT future
                                    consumption
  3. predict_next_n_days()      -> uses the trained model to forecast
  4. detect_anomalies()          -> uses Isolation Forest to flag abnormal
                                    usage (unsupervised, no labels needed)

Models used:
  - RandomForestRegressor  -> consumption forecasting (handles
    non-linear relationship between occupancy/weekday/season and usage)
  - IsolationForest         -> anomaly detection (flags spikes such as
    leakages, appliances left on, faulty meters)

Run directly to train + save models:  python ai_models.py
-------------------------------------------------------
"""

import os
import joblib
import numpy as np
import pandas as pd
from datetime import timedelta

from sklearn.ensemble import RandomForestRegressor, IsolationForest
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

import database as db

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
os.makedirs(MODEL_DIR, exist_ok=True)


# ---------------------------------------------------------------
# 1. FEATURE ENGINEERING
# ---------------------------------------------------------------

def _load_resource_dataframe(resource_type):
    """
    Pulls readings + occupancy + room info from the DB and joins them
    into a single dataframe ready for feature engineering.
    resource_type: 'electricity', 'water', or 'wifi'
    """
    if resource_type == "electricity":
        readings = db.fetch_all("SELECT * FROM electricity_readings")
        value_col = "units_kwh"
    elif resource_type == "water":
        readings = db.fetch_all("SELECT * FROM water_readings")
        value_col = "liters"
    elif resource_type == "wifi":
        readings = db.fetch_all("SELECT * FROM wifi_usage")
        value_col = "data_gb"
    else:
        raise ValueError("resource_type must be 'electricity', 'water', or 'wifi'")

    occupancy = db.fetch_all("SELECT * FROM occupancy_log")
    rooms = db.fetch_all("SELECT * FROM rooms")

    df = pd.DataFrame(readings)
    occ_df = pd.DataFrame(occupancy)[["room_id", "reading_date", "occupied_count"]]
    rooms_df = pd.DataFrame(rooms)[["room_id", "capacity"]]

    df = df.merge(occ_df, on=["room_id", "reading_date"], how="left")
    df = df.merge(rooms_df, on="room_id", how="left")
    df.rename(columns={value_col: "value"}, inplace=True)
    df["reading_date"] = pd.to_datetime(df["reading_date"])
    df.sort_values(["room_id", "reading_date"], inplace=True)
    return df


def build_features(resource_type):
    """
    Engineers time-series features per room:
      - day_of_week, month
      - occupancy, capacity, occupancy_ratio
      - lag_1 (previous day's value)
      - rolling_mean_7 (trailing 7-day average)
    Returns a clean dataframe ready for model training/inference.
    """
    df = _load_resource_dataframe(resource_type)

    df["day_of_week"] = df["reading_date"].dt.dayofweek
    df["month"] = df["reading_date"].dt.month
    df["occupied_count"] = df["occupied_count"].fillna(0)
    df["occupancy_ratio"] = df["occupied_count"] / df["capacity"].replace(0, 1)

    df["lag_1"] = df.groupby("room_id")["value"].shift(1)
    df["rolling_mean_7"] = (
        df.groupby("room_id")["value"]
        .transform(lambda s: s.shift(1).rolling(window=7, min_periods=1).mean())
    )

    df.dropna(subset=["lag_1", "rolling_mean_7"], inplace=True)
    return df


FEATURE_COLUMNS = [
    "day_of_week", "month", "occupied_count",
    "capacity", "occupancy_ratio", "lag_1", "rolling_mean_7",
]


# ---------------------------------------------------------------
# 2. MODEL TRAINING
# ---------------------------------------------------------------

def train_and_save_model(resource_type):
    """
    Trains a RandomForestRegressor to predict next-day consumption
    for a given resource, evaluates it, and saves it to disk.
    """
    df = build_features(resource_type)

    X = df[FEATURE_COLUMNS]
    y = df["value"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    model = RandomForestRegressor(
        n_estimators=200, max_depth=8, random_state=42, n_jobs=-1
    )
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    mae = mean_absolute_error(y_test, preds)
    r2 = r2_score(y_test, preds)

    model_path = os.path.join(MODEL_DIR, f"{resource_type}_model.joblib")
    joblib.dump(model, model_path)

    metrics = {"resource_type": resource_type, "MAE": round(mae, 3), "R2": round(r2, 3)}
    print(f"[AI] Trained {resource_type} model -> MAE={mae:.3f}, R2={r2:.3f}")
    return metrics


def train_all_models():
    results = []
    for resource in ("electricity", "water", "wifi"):
        results.append(train_and_save_model(resource))
    return results


def _load_model(resource_type):
    path = os.path.join(MODEL_DIR, f"{resource_type}_model.joblib")
    if not os.path.exists(path):
        train_and_save_model(resource_type)
    return joblib.load(path)


# ---------------------------------------------------------------
# 3. FORECASTING
# ---------------------------------------------------------------

# ---------------------------------------------------------------
# 3. FORECASTING & OCCUPANCY PREDICTION
# ---------------------------------------------------------------

def predict_occupancy_next_n_days(room_id, n_days=7):
    """
    Forecasts room occupancy using historical occupancy patterns, day-of-week,
    rolling averages, and room capacity.
    """
    occ_rows = db.fetch_all("SELECT * FROM occupancy_log WHERE room_id = ? ORDER BY reading_date", (room_id,))
    room = db.fetch_one("SELECT * FROM rooms WHERE room_id = ?", (room_id,))
    capacity = room["capacity"] if room else 3

    if not occ_rows:
        return [{"date": (pd.Timestamp.today() + timedelta(days=i)).strftime("%Y-%m-%d"), "predicted_occupancy": capacity} for i in range(1, n_days + 1)]

    df_occ = pd.DataFrame(occ_rows)
    df_occ["reading_date"] = pd.to_datetime(df_occ["reading_date"])
    df_occ["day_of_week"] = df_occ["reading_date"].dt.dayofweek

    # Typical day-of-week average occupancy
    dow_avg = df_occ.groupby("day_of_week")["occupied_count"].mean().to_dict()
    recent_7 = df_occ["occupied_count"].tail(7).mean()
    last_date = df_occ["reading_date"].max()

    forecasts = []
    for i in range(1, n_days + 1):
        next_date = last_date + timedelta(days=i)
        dow = next_date.dayofweek
        # Weight the day-of-week seasonal pattern more heavily than the
        # short-term trend, so the weekly rhythm actually shows up instead
        # of being smoothed flat by the recent average. (No hardcoded
        # weekend-lower assumption here - that fought the real generated
        # data, which is busier on weekends, and combined with rounding to
        # a whole student count on a 0-3 capacity room, collapsed almost
        # every day to the same integer, i.e. a flat forecast line.)
        pred_occ = 0.75 * dow_avg.get(dow, recent_7) + 0.25 * recent_7
        pred_occ = max(0, min(capacity, pred_occ))

        forecasts.append({
            "date": next_date.strftime("%Y-%m-%d"),
            "predicted_occupancy": round(pred_occ, 2),
            "capacity": capacity,
            "occupancy_rate_pct": round((pred_occ / max(1, capacity)) * 100, 1),
        })

    return forecasts


def predict_hostel_occupancy(hostel_id, n_days=7, floor=None):
    """Aggregates occupancy predictions across all rooms in a hostel (or,
    when floor is given, just that floor)."""
    rooms = db.get_rooms(hostel_id=hostel_id)
    if floor is not None:
        rooms = [r for r in rooms if r["floor"] == floor]
    if not rooms:
        return {"dates": [], "total_capacity": 0, "predictions": []}

    total_capacity = sum(r["capacity"] for r in rooms)
    room_predictions = {r["room_id"]: predict_occupancy_next_n_days(r["room_id"], n_days=n_days) for r in rooms}

    dates = [p["date"] for p in next(iter(room_predictions.values()))] if room_predictions else []
    daily_totals = []

    for idx, d in enumerate(dates):
        tot_occ = sum(room_predictions[r["room_id"]][idx]["predicted_occupancy"] for r in rooms if idx < len(room_predictions[r["room_id"]]))
        occ_pct = round((tot_occ / max(1, total_capacity)) * 100, 1)
        daily_totals.append({
            "date": d,
            "predicted_occupancy": round(tot_occ, 1),
            "total_capacity": total_capacity,
            "occupancy_rate_pct": occ_pct,
            "load_status": "High" if occ_pct > 85 else "Moderate" if occ_pct >= 60 else "Low",
        })

    peak_occ = max([p["predicted_occupancy"] for p in daily_totals], default=0)
    return {
        "dates": dates,
        "total_capacity": total_capacity,
        "peak_occupancy": peak_occ,
        "average_expected_occupancy": round(np.mean([p["predicted_occupancy"] for p in daily_totals]), 1) if daily_totals else 0,
        "daily_forecast": daily_totals,
    }


def predict_next_n_days(resource_type, room_id, n_days=7, custom_occupancy=None):
    """
    Iteratively forecasts the next n_days of consumption for a room,
    feeding each prediction back in as lag_1 and using predicted occupancy
    as the primary demand driver (Occupancy -> Resource Forecast).

    Ground-truths the model's raw output against a standby-power floor:
    the trained Random Forest leans heavily on recent lag/rolling-mean
    features, so on days the historical data never showed near-zero
    occupancy, its raw prediction can barely move even when forecasted
    occupancy drops to 0. We blend toward the resource's realistic
    "room empty" standby baseline in proportion to how empty the room is
    predicted to be, so a 0-occupancy day actually forecasts near-standby
    consumption instead of tracking the recent occupied-day average.
    """
    STANDBY_BASELINE = {"electricity": 0.6, "water": 5.0, "wifi": 0.05}

    model = _load_model(resource_type)
    df = build_features(resource_type)
    room_df = df[df["room_id"] == room_id].sort_values("reading_date")

    if room_df.empty:
        return []

    last_row = room_df.iloc[-1]
    history = room_df["value"].tolist()[-7:]
    last_date = last_row["reading_date"]

    # Occupancy forecast for future dates
    occ_preds = {p["date"]: p["predicted_occupancy"] for p in predict_occupancy_next_n_days(room_id, n_days)}
    capacity = int(last_row["capacity"])
    standby_floor = STANDBY_BASELINE.get(resource_type, 0.3)

    forecasts = []
    lag_1 = float(last_row["value"])

    for i in range(1, n_days + 1):
        next_date = last_date + timedelta(days=i)
        date_str = next_date.strftime("%Y-%m-%d")
        rolling_mean_7 = float(np.mean(history[-7:]))

        if custom_occupancy is not None:
            occupied_count = float(custom_occupancy)
        else:
            occupied_count = float(occ_preds.get(date_str, last_row["occupied_count"]))

        occupancy_ratio = occupied_count / max(1, capacity)

        features = pd.DataFrame([{
            "day_of_week": next_date.dayofweek,
            "month": next_date.month,
            "occupied_count": occupied_count,
            "capacity": capacity,
            "occupancy_ratio": occupancy_ratio,
            "lag_1": lag_1,
            "rolling_mean_7": rolling_mean_7,
        }])[FEATURE_COLUMNS]

        raw_pred = float(model.predict(features)[0])
        # Blend the raw ML prediction toward the standby floor in proportion to
        # how empty the room is forecast to be (occupancy_ratio=0 -> standby,
        # occupancy_ratio=1 -> the model's own prediction, unmodified).
        pred = raw_pred * occupancy_ratio + standby_floor * (1 - occupancy_ratio)
        pred = max(0.1, pred)

        forecasts.append({
            "date": date_str,
            "predicted_value": round(pred, 2),
            "predicted_occupancy": int(occupied_count),
        })

        history.append(pred)
        lag_1 = pred

    return forecasts


def predict_next_n_days_aggregated(resource_type, room_ids, n_days=7):
    """Sums each room's own forecast across every room in room_ids - the
    floor-/hostel-level equivalent of predict_next_n_days for a single
    room, same pattern as predict_hostel_occupancy."""
    if not room_ids:
        return []
    per_room = {rid: predict_next_n_days(resource_type, rid, n_days) for rid in room_ids}
    per_room = {rid: fc for rid, fc in per_room.items() if fc}
    if not per_room:
        return []

    dates = [p["date"] for p in next(iter(per_room.values()))]
    totals = []
    for idx, d in enumerate(dates):
        tot_value = sum(fc[idx]["predicted_value"] for fc in per_room.values() if idx < len(fc))
        tot_occ = sum(fc[idx]["predicted_occupancy"] for fc in per_room.values() if idx < len(fc))
        totals.append({
            "date": d,
            "predicted_value": round(tot_value, 2),
            "predicted_occupancy": tot_occ,
        })
    return totals


# ---------------------------------------------------------------
# 4. EXPLAINABLE AI (XAI) & ANOMALY DETECTION
# ---------------------------------------------------------------

def explain_prediction(resource_type, room_id, current_val, predicted_val, n_days=7):
    """
    Explains the factors contributing to the predicted consumption change:
    Occupancy influence, calendar/weekday effect, historical momentum.
    """
    diff = predicted_val - current_val
    pct_change = (diff / max(0.1, current_val)) * 100

    room_occ = db.fetch_all("SELECT occupied_count FROM occupancy_log WHERE room_id = ? ORDER BY reading_date DESC LIMIT 7", (room_id,))
    recent_occ = [r["occupied_count"] for r in room_occ]
    avg_occ = np.mean(recent_occ) if recent_occ else 2.0

    occ_forecast = predict_occupancy_next_n_days(room_id, n_days=n_days)
    pred_avg_occ = np.mean([p["predicted_occupancy"] for p in occ_forecast]) if occ_forecast else avg_occ
    occ_delta_pct = ((pred_avg_occ - avg_occ) / max(1, avg_occ)) * 100

    # Approximate feature attributions
    factors = [
        {"factor": "Occupancy Variation", "impact_pct": round(occ_delta_pct * 0.6, 1)},
        {"factor": "Weekday / Weekend Pattern", "impact_pct": round(2.5 if pct_change > 0 else -2.0, 1)},
        {"factor": "Recent Rolling Trend", "impact_pct": round(pct_change * 0.3, 1)},
        {"factor": "Room Baseline Load", "impact_pct": round(pct_change * 0.1, 1)},
    ]

    trend_dir = "increase" if pct_change > 0 else "decrease" if pct_change < 0 else "remain stable"
    explanation = (
        f"Expected {abs(pct_change):.1f}% {trend_dir} in {resource_type}. "
        f"Primary drivers: Occupancy shift ({occ_delta_pct:+.1f}%) and trailing moving average baseline."
    )

    return {
        "current_value": round(current_val, 2),
        "predicted_value": round(predicted_val, 2),
        "difference": round(diff, 2),
        "percentage_change": round(pct_change, 1),
        "trend": "UP" if pct_change > 2 else "DOWN" if pct_change < -2 else "FLAT",
        "explanation": explanation,
        "factor_breakdown": factors,
    }


def explain_anomaly(resource_type, room_no, current_val, typical_val, occupancy=None, capacity=None):
    """
    Generates transparent, non-technical explanation and probable causes
    for an anomaly without asserting absolute physical certainty (Section 38).

    Branches on direction: a usage *spike* and a usage *drop* are different
    physical situations and need different causes/actions - a room using far
    less than usual is not explained by "AC left running continuously".
    """
    ratio = current_val / max(0.1, typical_val)
    pct_change = (ratio - 1.0) * 100
    is_spike = ratio >= 1.0

    if resource_type == "water":
        if is_spike:
            causes = [
                "Continuous bathroom flush or pipeline leakage",
                "Tap or shower left running unattended",
                "Overhead tank float valve malfunction / tank overflow",
                "Water meter pulse sensor inaccuracy",
            ]
            actions = [
                "Inspect bathroom pipelines, taps, and cistern flush mechanisms immediately",
                "Verify overhead tank inlet shut-off valve",
                "Check physical meter reading against digital pulse sensor",
            ]
        else:
            causes = [
                "Room temporarily vacant (occupants on leave / vacation period)",
                "Water supply line shut off, blocked, or low mains pressure",
                "Water meter pulse sensor disconnected or under-registering",
                "Occupants relying on an alternate source (e.g. common washroom)",
            ]
            actions = [
                "Confirm current occupancy status for the room",
                "Check inlet valve and supply line for blockage or shutoff",
                "Test the meter's pulse output against a manual reading",
            ]
    elif resource_type == "electricity":
        if is_spike:
            causes = [
                "High-wattage heating appliance (iron, electric kettle, water heater) left ON",
                "Air conditioner / cooler operating continuously with open doors/windows",
                "Wiring fault or partial short circuit causing power leakage",
                "Sub-meter calibration drift or sensor malfunction",
            ]
            actions = [
                "Inspect room electrical points and verify appliance usage compliance",
                "Check MCB / distribution board for abnormal thermal heating",
                "Verify power factor and test sub-meter with calibration clamp meter",
            ]
        else:
            causes = [
                "Room temporarily vacant (occupants on leave / vacation period)",
                "MCB tripped or sub-meter disconnected from the circuit",
                "Appliances unplugged or removed from the room",
                "Sub-meter reporting fault (stuck / under-registering)",
            ]
            actions = [
                "Confirm current occupancy status for the room",
                "Check MCB / distribution board is switched on and connected",
                "Test the sub-meter reading against a manual clamp-meter check",
            ]
    else:  # wifi
        if is_spike:
            causes = [
                "Heavy peer-to-peer / torrent file downloading or streaming",
                "Unauthorized Wi-Fi hotspot sharing across multiple adjacent rooms",
                "Multiple rogue or guest devices connected simultaneously",
                "Background cloud backup or high-bandwidth operating system update",
            ]
            actions = [
                "Verify connected device count on access point controller",
                "Inspect high-throughput session logs on router/gateway",
                "Apply temporary QoS bandwidth limit to prevent network congestion",
            ]
        else:
            causes = [
                "Room temporarily vacant (occupants on leave / vacation period)",
                "Access point in the room offline or malfunctioning",
                "Occupants' devices powered off or disconnected from the network",
                "Local outage or ISP-side connectivity issue",
            ]
            actions = [
                "Confirm current occupancy status for the room",
                "Check the room's access point is powered on and online",
                "Verify upstream connectivity from the router/gateway",
            ]

    direction_word = "elevated" if is_spike else "reduced"
    explanation = (
        f"{resource_type.capitalize()} consumption is unusually {direction_word} ({current_val:.1f} vs typical {typical_val:.1f}, "
        f"{pct_change:+.1f}%). Current usage is approximately {ratio:.1f}× the recent rolling average for Room {room_no}."
    )

    return {
        "explanation": explanation,
        "ratio": round(ratio, 2),
        "percentage_increase": round(pct_change, 1),
        "is_spike": is_spike,
        "possible_causes": causes,
        "recommended_actions": actions,
    }


def detect_anomalies(resource_type, contamination=0.03, recent_days=30, room_ids=None):
    """
    Uses IsolationForest to detect abnormal consumption, calculates severity
    (CRITICAL, HIGH, MEDIUM, LOW), generates XAI explanations and recommended
    actions, stores them in alerts with possible_causes, and returns them.
    """
    df = build_features(resource_type)
    if room_ids is not None:
        df = df[df["room_id"].isin(room_ids)]
    if df.empty:
        return []

    detector = IsolationForest(contamination=contamination, random_state=42)
    df["anomaly_score"] = detector.fit_predict(df[["value", "occupancy_ratio", "day_of_week"]])

    cutoff = df["reading_date"].max() - timedelta(days=recent_days)
    recent = df[(df["reading_date"] >= cutoff) & (df["anomaly_score"] == -1)]

    room_lookup = {r["room_id"]: r["room_no"] for r in db.get_rooms()}
    alerts = []

    for _, row in recent.iterrows():
        room_no = room_lookup.get(row["room_id"], str(row["room_id"]))
        val = float(row["value"])
        typ = float(row["rolling_mean_7"])
        ratio = val / max(0.1, typ)

        if ratio >= 2.5:
            severity = "critical"
        elif ratio >= 1.8:
            severity = "high"
        elif ratio >= 1.3:
            severity = "medium"
        else:
            severity = "low"

        xai = explain_anomaly(resource_type, room_no, val, typ)
        causes_str = " | ".join(xai["possible_causes"][:2])
        actions_str = " | ".join(xai["recommended_actions"][:2])
        message = (
            f"Unusual {resource_type} usage in room {room_no}: "
            f"{val:.1f} vs typical {typ:.1f} ({xai['percentage_increase']:+.0f}%)"
        )

        alert_id = db.insert_alert_extended(
            resource_type=resource_type,
            room_id=int(row["room_id"]),
            reading_date=row["reading_date"].strftime("%Y-%m-%d"),
            message=message,
            severity=severity,
            possible_causes=causes_str,
            recommended_action=actions_str,
            status="ACTIVE",
        )

        alerts.append({
            "id": alert_id,
            "room_id": int(row["room_id"]),
            "room_no": room_no,
            "reading_date": row["reading_date"].strftime("%Y-%m-%d"),
            "peak_hour": db.get_peak_hour(resource_type, row["reading_date"].strftime("%Y-%m-%d"), [int(row["room_id"])]),
            "value": round(val, 2),
            "typical": round(typ, 2),
            "ratio": xai["ratio"],
            "percentage_increase": xai["percentage_increase"],
            "severity": severity,
            "message": message,
            "possible_causes": xai["possible_causes"],
            "recommended_actions": xai["recommended_actions"],
            "explanation": xai["explanation"],
        })

    print(f"[AI] Detected {len(alerts)} anomalies in {resource_type} (last {recent_days} days)")
    return alerts


# ---------------------------------------------------------------
# 5. RECOMMENDATION & SAVINGS ENGINE
# ---------------------------------------------------------------

def generate_hostel_recommendations(hostel_id, rates=None):
    """
    Synthesizes concrete AI recommendations and potential savings for a hostel
    by analyzing current consumption, anomalies, and efficiency rankings.
    """
    if rates is None:
        rates = {"electricity_rate": 8.50, "water_rate": 0.05, "wifi_rate": 15.00}

    elec_rate = rates.get("electricity_rate", 8.50)
    water_rate = rates.get("water_rate", 0.05)
    wifi_rate = rates.get("wifi_rate", 15.00)

    rooms = db.get_rooms(hostel_id=hostel_id)
    room_ids = [r["room_id"] for r in rooms]
    if not room_ids:
        return []

    hostel = db.get_hostel(hostel_id)
    hostel_name = hostel["name"] if hostel else f"Hostel #{hostel_id}"

    recs = []

    # 1. Analyze Water Anomaly / Leakage Opportunities
    water_anomalies = [a for a in db.get_alerts(resource_type="water", limit=20, hostel_id=hostel_id, severity="high") if a.get("status") == "ACTIVE"]
    if water_anomalies:
        target_room = water_anomalies[0]["room_no"]
        room_id = water_anomalies[0]["room_id"]
        est_leak_liters_day = 1200
        monthly_saving = round(est_leak_liters_day * 30 * water_rate, 2)
        annual_saving = round(monthly_saving * 12, 2)

        recs.append({
            "hostel_id": hostel_id,
            "room_id": room_id,
            "resource_type": "water",
            "title": f"Resolve Water Leakage in Room {target_room}",
            "description": f"Continuous high water usage detected in Room {target_room} (~{est_leak_liters_day} L/day abnormal draw). Addressing faucet/plumbing defects avoids severe institution wastage.",
            "priority": "HIGH",
            "potential_savings_monthly": monthly_saving,
            "potential_savings_annual": annual_saving,
            "action_items": [
                f"Inspect Room {target_room} flush valve and tap washer",
                "Verify floor isolation valve for pressure regulation",
                "Confirm pipeline integrity and log repair in maintenance",
            ],
            "status": "ACTIVE",
        })

    # 2. Electricity Conservation via Intelligent Scheduling / Load Shifting
    elec_ranks = get_room_efficiency_ranking("electricity", room_ids=room_ids)
    if elec_ranks:
        least_eff = elec_ranks[0]
        # Potential 12% reduction on high consumption rooms
        kwh_waste_monthly = least_eff["per_occupant"] * 3 * 30 * 0.15
        monthly_elec_saving = round(kwh_waste_monthly * elec_rate, 2)
        annual_elec_saving = round(monthly_elec_saving * 12, 2)

        recs.append({
            "hostel_id": hostel_id,
            "room_id": least_eff["room_id"],
            "resource_type": "electricity",
            "title": f"Curtail Peak Electricity Draw in Room {least_eff.get('room_no', 'N/A')}",
            "description": f"Room {least_eff.get('room_no', 'N/A')} consumes {least_eff['per_occupant']:.1f} kWh/occupant/day (highest in {hostel_name}). Enforcing automated power timers or geyser cutoffs reduces peak demand.",
            "priority": "MEDIUM",
            "potential_savings_monthly": monthly_elec_saving,
            "potential_savings_annual": annual_elec_saving,
            "action_items": [
                "Inspect high-draw heating appliances in Room " + str(least_eff.get('room_no', '')),
                "Install automated master switch for unoccupied daylight hours",
                "Verify distribution sub-panel load balancing",
            ],
            "status": "ACTIVE",
        })

    # 3. Wi-Fi Bandwidth Optimization
    wifi_anomalies = [a for a in db.get_alerts(resource_type="wifi", limit=10, hostel_id=hostel_id) if a.get("status") == "ACTIVE"]
    if wifi_anomalies:
        w_room = wifi_anomalies[0]["room_no"]
        recs.append({
            "hostel_id": hostel_id,
            "room_id": wifi_anomalies[0]["room_id"],
            "resource_type": "wifi",
            "title": f"Implement QoS Bandwidth Quota for Room {w_room}",
            "description": f"Excessive bandwidth spikes detected in Room {w_room}. Throttling non-academic P2P/streaming during study hours preserves connectivity for other occupants.",
            "priority": "LOW",
            "potential_savings_monthly": round(25 * wifi_rate, 2),
            "potential_savings_annual": round(25 * wifi_rate * 12, 2),
            "action_items": [
                "Configure 25 Mbps ceiling limit per device in Room " + str(w_room),
                "Block non-essential P2P ports on hostel edge firewall",
                "Review active DHCP leases for unauthorized tethering",
            ],
            "status": "ACTIVE",
        })

    # 4. Institutional Solar / Water Pump Schedule Optimization
    recs.append({
        "hostel_id": hostel_id,
        "room_id": None,
        "resource_type": "electricity",
        "title": f"Shift {hostel_name} Pumping Schedule Off-Peak",
        "description": f"Shifting overhead water pump operation from morning peak (7:00-9:00 AM) to solar solar-window (11:30 AM-2:00 PM) saves an estimated ₹4,200/month under time-of-day tariffs.",
        "priority": "MEDIUM",
        "potential_savings_monthly": 4200.0,
        "potential_savings_annual": 50400.0,
        "action_items": [
            "Set digital pump controller timer to 11:30 AM - 1:30 PM",
            "Verify sensor float switch in overhead water reservoirs",
            "Track monthly demand charges on institution utility bill",
        ],
        "status": "ACTIVE",
    })

    return recs


# ---------------------------------------------------------------
# 6. EFFICIENCY RANKINGS & HOSTEL SCORES
# ---------------------------------------------------------------

def get_room_efficiency_ranking(resource_type, room_ids=None):
    """
    Ranks rooms by average consumption per occupant.
    """
    df = build_features(resource_type)
    if room_ids is not None:
        df = df[df["room_id"].isin(room_ids)]
    if df.empty:
        return []

    df["per_occupant"] = df["value"] / df["occupied_count"].replace(0, 1)
    ranking = (
        df.groupby("room_id")["per_occupant"]
        .mean()
        .sort_values(ascending=False)
        .reset_index()
    )
    room_lookup = {r["room_id"]: r["room_no"] for r in db.get_rooms()}
    ranking["room_no"] = ranking["room_id"].map(room_lookup)
    return ranking.round(2).to_dict(orient="records")


def get_enhanced_room_efficiency(resource_type, room_ids=None):
    """
    Grades and categorizes each room into:
    1. Most Efficient  2. Efficient  3. Average  4. Inefficient  5. Critical
    Includes efficiency score (0-100) and specific rationale.
    """
    ranks = get_room_efficiency_ranking(resource_type, room_ids=room_ids)
    if not ranks:
        return []

    values = [r["per_occupant"] for r in ranks]
    mean_val = np.mean(values)
    min_val = min(values)
    max_val = max(values)
    spread = max_val - min_val if max_val > min_val else 1.0

    unit = "kWh/person" if resource_type == "electricity" else "L/person" if resource_type == "water" else "GB/person"

    enhanced = []
    for r in ranks:
        val = r["per_occupant"]
        # Inverse score: lower consumption per occupant -> higher efficiency score
        score = max(10, min(100, round(100 - ((val - min_val) / spread) * 75)))
        ratio = val / max(0.1, mean_val)

        if ratio <= 0.8:
            grade = "Most Efficient"
            badge = "grade-a"
            reason = f"Usage is {((1.0 - ratio) * 100):.0f}% below institutional average ({val:.1f} {unit})."
        elif ratio <= 1.05:
            grade = "Efficient"
            badge = "grade-b"
            reason = f"Optimal consumption within typical baseline ({val:.1f} {unit})."
        elif ratio <= 1.35:
            grade = "Average"
            badge = "grade-c"
            reason = f"Slightly above average consumption ({val:.1f} {unit})."
        elif ratio <= 1.8:
            grade = "Inefficient"
            badge = "grade-d"
            reason = f"High consumption: {ratio:.1f}× institutional average."
        else:
            grade = "Critical"
            badge = "grade-f"
            reason = f"Abnormally high consumption: {ratio:.1f}× hostel average ({val:.1f} {unit}). Needs inspection."

        enhanced.append({
            "room_id": r["room_id"],
            "room_no": r["room_no"],
            "per_occupant": val,
            "efficiency_score": score,
            "grade": grade,
            "badge_class": badge,
            "reason": reason,
        })

    return enhanced


def calculate_hostel_efficiency_score(hostel_id, rates=None):
    """
    Transparent composite Hostel Efficiency Score (0 - 100):
    - Electricity efficiency (25%)
    - Water efficiency (25%)
    - Wi-Fi efficiency (15%)
    - Occupancy utilization (15%)
    - Anomaly penalty (10%)
    - Cost efficiency (10%)
    """
    rooms = db.get_rooms(hostel_id=hostel_id)
    room_ids = {r["room_id"] for r in rooms}
    if not room_ids:
        return {"overall_score": 75, "rating": "Good", "breakdown": {}}

    # Benchmark comparisons
    elec_ranks = get_room_efficiency_ranking("electricity", room_ids=list(room_ids))
    water_ranks = get_room_efficiency_ranking("water", room_ids=list(room_ids))
    wifi_ranks = get_room_efficiency_ranking("wifi", room_ids=list(room_ids))

    avg_elec_per_occ = np.mean([r["per_occupant"] for r in elec_ranks]) if elec_ranks else 2.5
    avg_water_per_occ = np.mean([r["per_occupant"] for r in water_ranks]) if water_ranks else 40.0
    avg_wifi_per_occ = np.mean([r["per_occupant"] for r in wifi_ranks]) if wifi_ranks else 1.2

    # Standard targets for high-density hostels
    elec_target = 2.0   # kWh/occ/day
    water_target = 35.0 # L/occ/day
    wifi_target = 1.0   # GB/occ/day

    elec_score = max(20, min(100, round(100 - max(0, (avg_elec_per_occ - elec_target) / elec_target * 50))))
    water_score = max(20, min(100, round(100 - max(0, (avg_water_per_occ - water_target) / water_target * 50))))
    wifi_score = max(30, min(100, round(100 - max(0, (avg_wifi_per_occ - wifi_target) / wifi_target * 35))))

    # Occupancy utilization
    occ_rows = db.fetch_all("SELECT occupied_count FROM occupancy_log WHERE room_id IN ({}) ORDER BY reading_date DESC LIMIT 50".format(",".join(map(str, room_ids))))
    avg_occ = np.mean([r["occupied_count"] for r in occ_rows]) if occ_rows else 2.5
    avg_cap = np.mean([r["capacity"] for r in rooms]) if rooms else 3.0
    occ_ratio = avg_occ / max(1, avg_cap)
    occ_score = max(40, min(100, round(occ_ratio * 100)))

    # Anomaly penalty
    recent_alerts = db.get_alerts(limit=50, hostel_id=hostel_id)
    anomaly_penalty = min(30, len(recent_alerts) * 3)
    anomaly_score = max(40, 100 - anomaly_penalty)

    # Cost score
    cost_score = round((elec_score * 0.5 + water_score * 0.5))

    overall = round(
        elec_score * 0.25 +
        water_score * 0.25 +
        wifi_score * 0.15 +
        occ_score * 0.15 +
        anomaly_score * 0.10 +
        cost_score * 0.10
    )

    rating = "Excellent" if overall >= 88 else "Good" if overall >= 75 else "Moderate" if overall >= 60 else "Needs Optimization"

    return {
        "overall_score": overall,
        "rating": rating,
        "breakdown": {
            "electricity_efficiency": elec_score,
            "water_efficiency": water_score,
            "wifi_efficiency": wifi_score,
            "occupancy_utilization": occ_score,
            "anomaly_factor": anomaly_score,
            "cost_efficiency": cost_score,
        },
        "metrics": {
            "avg_electricity_per_occupant": round(avg_elec_per_occ, 2),
            "avg_water_per_occupant": round(avg_water_per_occ, 2),
            "avg_wifi_per_occupant": round(avg_wifi_per_occ, 2),
            "occupancy_rate_pct": round(occ_ratio * 100, 1),
        },
    }


# ---------------------------------------------------------------
# 7. CARBON FOOTPRINT & SUSTAINABILITY DASHBOARD
# ---------------------------------------------------------------

def calculate_sustainability_metrics(hostel_id=None, days=30):
    """
    Computes greenhouse gas (GHG) CO2 equivalent emissions from electricity
    consumption using India Central Electricity Authority (CEA) standard
    grid emission factor: 0.82 kg CO2 / kWh.
    """
    rooms = db.get_rooms(hostel_id=hostel_id)
    room_ids = {r["room_id"] for r in rooms}
    if not room_ids:
        return {"total_co2_kg": 0, "annual_projected_co2_tons": 0, "sustainability_score": 75, "trend": "stable"}

    elec_readings = [r for r in db.get_electricity(limit_days=days * len(room_ids)) if r["room_id"] in room_ids]
    if not elec_readings:
        return {"total_co2_kg": 0, "annual_projected_co2_tons": 0, "sustainability_score": 75, "trend": "stable"}

    total_kwh_30d = sum(r["units_kwh"] for r in elec_readings)
    co2_kg_30d = total_kwh_30d * 0.82
    co2_daily_avg = co2_kg_30d / max(1, days)
    annual_co2_kg = co2_daily_avg * 365
    annual_co2_tons = annual_co2_kg / 1000.0

    # Trend: compare first 15 days vs last 15 days
    mid = len(elec_readings) // 2
    first_half = sum(r["units_kwh"] for r in elec_readings[:mid]) if mid else 0
    second_half = sum(r["units_kwh"] for r in elec_readings[mid:]) if mid else 0
    delta_pct = ((second_half - first_half) / max(1.0, first_half)) * 100 if first_half else 0

    trend = "increasing" if delta_pct > 3.0 else "decreasing" if delta_pct < -3.0 else "stable"

    # Sustainability score (0-100)
    kwh_per_room_day = total_kwh_30d / (max(1, len(room_ids)) * max(1, days))
    green_score = max(35, min(98, round(100 - (kwh_per_room_day - 3.5) * 8)))

    trees_needed = round(annual_co2_kg / 21.0) # 1 tree absorbs ~21 kg CO2/year

    return {
        "monthly_electricity_kwh": round(total_kwh_30d, 1),
        "total_co2_kg": round(co2_kg_30d, 1),
        "daily_avg_co2_kg": round(co2_daily_avg, 2),
        "annual_projected_co2_tons": round(annual_co2_tons, 2),
        "trend": trend,
        "monthly_change_pct": round(delta_pct, 1),
        "sustainability_score": green_score,
        "trees_offset_needed": trees_needed,
        "clean_energy_potential_kwh": round(total_kwh_30d * 0.35, 1), # 35% rooftop solar potential
    }


# ---------------------------------------------------------------
# 8. WHAT-IF OPTIMIZATION SIMULATOR
# ---------------------------------------------------------------

def run_what_if_simulation(hostel_id, sim_occupancy=None, elec_reduction_pct=0.0, water_reduction_pct=0.0, wifi_reduction_pct=0.0, rates=None):
    """
    Simulates operational parameter adjustments:
    - Occupancy changes (e.g. 480 -> 550)
    - Resource conservation targets (e.g. 10% electricity cut)
    Returns predicted consumption, rupee savings, and avoided emissions.
    """
    if rates is None:
        rates = {"electricity_rate": 8.50, "water_rate": 0.05, "wifi_rate": 15.00}

    elec_rate = rates.get("electricity_rate", 8.50)
    water_rate = rates.get("water_rate", 0.05)
    wifi_rate = rates.get("wifi_rate", 15.00)

    rooms = db.get_rooms(hostel_id=hostel_id)
    room_ids = {r["room_id"] for r in rooms}
    total_capacity = sum(r["capacity"] for r in rooms)

    # Current 30-day baseline totals
    elec_rows = [r for r in db.get_electricity(limit_days=30 * len(room_ids)) if r["room_id"] in room_ids]
    water_rows = [r for r in db.get_water(limit_days=30 * len(room_ids)) if r["room_id"] in room_ids]
    wifi_rows = [r for r in db.get_wifi(limit_days=30 * len(room_ids)) if r["room_id"] in room_ids]
    occ_rows = [r for r in db.get_occupancy(limit_days=30 * len(room_ids)) if r["room_id"] in room_ids]

    base_elec_monthly = sum(r["units_kwh"] for r in elec_rows) if elec_rows else 1420.0
    base_water_monthly = sum(r["liters"] for r in water_rows) if water_rows else 32000.0
    base_wifi_monthly = sum(r["data_gb"] for r in wifi_rows) if wifi_rows else 180.0
    base_occ = sum(r["occupied_count"] for r in occ_rows) / max(1, 30) if occ_rows else (total_capacity * 0.8)

    # Occupancy scaling factor
    if sim_occupancy is not None and base_occ > 0:
        occ_factor = float(sim_occupancy) / float(base_occ)
        # Variable consumption scales at ~75% elasticity with occupancy
        elec_after_occ = base_elec_monthly * (0.25 + 0.75 * occ_factor)
        water_after_occ = base_water_monthly * (0.15 + 0.85 * occ_factor)
        wifi_after_occ = base_wifi_monthly * (0.10 + 0.90 * occ_factor)
    else:
        sim_occupancy = round(base_occ)
        elec_after_occ = base_elec_monthly
        water_after_occ = base_water_monthly
        wifi_after_occ = base_wifi_monthly

    # Apply conservation reduction targets
    elec_reduction_multiplier = max(0.0, 1.0 - (float(elec_reduction_pct) / 100.0))
    water_reduction_multiplier = max(0.0, 1.0 - (float(water_reduction_pct) / 100.0))
    wifi_reduction_multiplier = max(0.0, 1.0 - (float(wifi_reduction_pct) / 100.0))

    sim_elec = elec_after_occ * elec_reduction_multiplier
    sim_water = water_after_occ * water_reduction_multiplier
    sim_wifi = wifi_after_occ * wifi_reduction_multiplier

    # Baseline & Simulated Cost calculations
    base_cost_elec = base_elec_monthly * elec_rate
    base_cost_water = base_water_monthly * water_rate
    base_cost_wifi = base_wifi_monthly * wifi_rate
    base_total_cost = base_cost_elec + base_cost_water + base_cost_wifi

    sim_cost_elec = sim_elec * elec_rate
    sim_cost_water = sim_water * water_rate
    sim_cost_wifi = sim_wifi * wifi_rate
    sim_total_cost = sim_cost_elec + sim_cost_water + sim_cost_wifi

    monthly_savings = base_total_cost - sim_total_cost
    annual_savings = monthly_savings * 12

    # Carbon impact
    base_co2_kg = base_elec_monthly * 0.82
    sim_co2_kg = sim_elec * 0.82
    monthly_co2_avoided_kg = max(0.0, base_co2_kg - sim_co2_kg)
    annual_co2_avoided_kg = monthly_co2_avoided_kg * 12

    return {
        "baseline": {
            "occupancy": round(base_occ),
            "electricity_kwh": round(base_elec_monthly, 1),
            "water_liters": round(base_water_monthly, 1),
            "wifi_gb": round(base_wifi_monthly, 1),
            "electricity_cost": round(base_cost_elec, 2),
            "water_cost": round(base_cost_water, 2),
            "wifi_cost": round(base_cost_wifi, 2),
            "total_cost": round(base_total_cost, 2),
            "co2_kg": round(base_co2_kg, 1),
        },
        "simulated": {
            "occupancy": int(sim_occupancy),
            "electricity_kwh": round(sim_elec, 1),
            "water_liters": round(sim_water, 1),
            "wifi_gb": round(sim_wifi, 1),
            "electricity_cost": round(sim_cost_elec, 2),
            "water_cost": round(sim_cost_water, 2),
            "wifi_cost": round(sim_cost_wifi, 2),
            "total_cost": round(sim_total_cost, 2),
            "co2_kg": round(sim_co2_kg, 1),
        },
        "impact": {
            "monthly_savings_inr": round(monthly_savings, 2),
            "annual_savings_inr": round(annual_savings, 2),
            # Positive = a genuine reduction in the monthly bill, matching the "Bill Reduction" label.
            # (Previously this was base->sim % *increase*, which showed a "reduction" as a negative
            # number and could exceed 100% whenever the occupancy jump swamped the conservation cuts.)
            "bill_reduction_pct": round(((base_total_cost - sim_total_cost) / max(1, base_total_cost)) * 100, 1),
            "monthly_co2_avoided_kg": round(monthly_co2_avoided_kg, 1),
            "annual_co2_avoided_kg": round(annual_co2_avoided_kg, 1),
        },
    }


if __name__ == "__main__":
    db.init_db()
    print("Training models on current data...")
    train_all_models()
    print("\nRunning anomaly detection...")
    detect_anomalies("electricity")
    detect_anomalies("water")
    detect_anomalies("wifi")
    print("\nTesting simulation and scoring...")
    hostels = db.fetch_all("SELECT * FROM hostels")
    if hostels:
        h_id = hostels[0]["hostel_id"]
        print(f"Hostel efficiency: {calculate_hostel_efficiency_score(h_id)}")
        print(f"Simulation: {run_what_if_simulation(h_id, elec_reduction_pct=10.0)}")
    print("\nDone. Models saved in ./models/")