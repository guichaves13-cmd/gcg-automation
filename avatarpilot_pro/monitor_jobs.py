import urllib.request
import json
import time
import sys

BASE_URL = "http://localhost:5052"
POLL_INTERVAL = 5
DURATION = 300  # 5 minutes

def fetch_json(url):
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return None

def main():
    print(f"[START] Monitoring {BASE_URL}/api/dashboard for new jobs (5 min, poll every 5s)", flush=True)

    # Get initial state
    dashboard = fetch_json(f"{BASE_URL}/api/dashboard")
    if dashboard is None:
        print("[ERROR] Could not reach /api/dashboard — server may be down or not running.", flush=True)
        sys.exit(1)

    # Try to determine initial count — handle various response shapes
    initial_count = None
    if isinstance(dashboard, dict):
        for key in ("total_generated", "total_jobs", "total", "count"):
            if key in dashboard:
                initial_count = dashboard[key]
                break
        if initial_count is None:
            # Try nested
            for val in dashboard.values():
                if isinstance(val, (int, float)):
                    initial_count = val
                    break

    if initial_count is None:
        # Fall back: snapshot history IDs
        history = fetch_json(f"{BASE_URL}/api/history")
        if isinstance(history, list):
            initial_count = len(history)
            print(f"[INFO] Using history length as baseline: {initial_count} jobs", flush=True)
        elif isinstance(history, dict):
            for key in ("total", "count", "total_generated"):
                if key in history:
                    initial_count = history[key]
                    break
            if initial_count is None:
                initial_count = 0
        else:
            initial_count = 0

    print(f"[INFO] Baseline job count: {initial_count}", flush=True)

    seen_ids = set()

    # Snapshot existing job IDs so we only report genuinely new ones
    history_now = fetch_json(f"{BASE_URL}/api/history")
    if isinstance(history_now, list):
        for job in history_now:
            jid = job.get("job_id") or job.get("id") or job.get("_id")
            if jid:
                seen_ids.add(str(jid))
    elif isinstance(history_now, dict):
        jobs_list = history_now.get("jobs") or history_now.get("history") or history_now.get("data") or []
        for job in jobs_list:
            jid = job.get("job_id") or job.get("id") or job.get("_id")
            if jid:
                seen_ids.add(str(jid))

    print(f"[INFO] Existing job IDs snapshotted: {len(seen_ids)}", flush=True)

    end_time = time.time() + DURATION
    current_count = initial_count

    while time.time() < end_time:
        time.sleep(POLL_INTERVAL)

        dashboard = fetch_json(f"{BASE_URL}/api/dashboard")
        if dashboard is None:
            print("[WARN] /api/dashboard unreachable, retrying...", flush=True)
            continue

        new_count = None
        if isinstance(dashboard, dict):
            for key in ("total_generated", "total_jobs", "total", "count"):
                if key in dashboard:
                    new_count = dashboard[key]
                    break

        # Regardless of count, always check history for new IDs
        history = fetch_json(f"{BASE_URL}/api/history")
        all_jobs = []
        if isinstance(history, list):
            all_jobs = history
        elif isinstance(history, dict):
            all_jobs = history.get("jobs") or history.get("history") or history.get("data") or []

        for job in all_jobs:
            jid = job.get("job_id") or job.get("id") or job.get("_id")
            if jid is None:
                continue
            jid_str = str(jid)
            if jid_str not in seen_ids:
                seen_ids.add(jid_str)
                # Extract fields
                duration = job.get("duration") or job.get("duration_seconds") or job.get("elapsed") or "N/A"
                status   = job.get("status") or job.get("state") or "N/A"
                voice    = job.get("voice") or job.get("voice_id") or job.get("voice_name") or "N/A"
                script   = job.get("script") or job.get("script_text") or job.get("text") or job.get("prompt") or ""
                preview  = (script[:120] + "...") if len(script) > 120 else script or "N/A"

                print(
                    f"[NEW JOB DETECTED]\n"
                    f"  job_id        : {jid_str}\n"
                    f"  status        : {status}\n"
                    f"  duration      : {duration}\n"
                    f"  voice         : {voice}\n"
                    f"  script_preview: {preview}",
                    flush=True
                )

        if new_count is not None and new_count != current_count:
            current_count = new_count

    print("[DONE] 5-minute monitoring window ended. No further events will be captured.", flush=True)

if __name__ == "__main__":
    main()
