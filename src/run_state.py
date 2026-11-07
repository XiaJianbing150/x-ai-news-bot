"""Durable per-day delivery journal, with conservative at-most-once reservation.

The workflow commits reservations BEFORE sending. A killed/ambiguous run stays
unknown and is not automatically replayed. This favors avoiding duplicates over
guaranteed delivery; operators must reconcile unknown outcomes manually.
"""
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import notify

CONTEXT_PATH = Path(".run/context.json")
PLAN_PATH = Path(".run/delivery-plan.json")


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_status(date):
    path = Path(f"archive/{date}.status.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_status(date, status):
    status["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(f"archive/{date}.status.json", status)


def _archive_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reserve_delivery(date):
    status = load_status(date)
    if not status or status.get("generation") != "ready":
        raise RuntimeError("Report is not ready for delivery")
    channels = notify._channels()
    if not channels:
        raise RuntimeError("未配置任何发送渠道")
    digest = _archive_hash(status["archive"])
    previous = status.setdefault("notifications", {})
    # Never auto-replay a channel with any prior attempt, including unknown.
    eligible = [channel for channel in channels if channel not in previous]
    reservation = uuid.uuid4().hex
    plan = {"date": date, "archive": status["archive"], "sha256": digest,
            "channels": eligible, "reservation": reservation}
    for channel in eligible:
        previous[channel] = {"status": "unknown", "reason": "reserved_before_send", "reservation": reservation}
    save_status(date, status)
    write_json(PLAN_PATH, plan)
    return plan


def deliver_reserved():
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    status = load_status(plan["date"])
    if _archive_hash(plan["archive"]) != plan["sha256"]:
        raise RuntimeError("Archive changed after delivery reservation")
    # One-shot local consumption is atomic, including overlapping send commands.
    # Keep this marker after a crash: the remote outcome can be ambiguous.
    claim = PLAN_PATH.parent / f"{plan['reservation']}.consumed"
    try:
        with claim.open("x", encoding="utf-8") as output:
            output.write("Do not replay this delivery plan.\n")
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        print("Delivery plan was already consumed; no messages resent.")
        return status
    notifications = status["notifications"]
    channels = [channel for channel in plan["channels"]
                if notifications.get(channel, {}).get("reservation") == plan["reservation"]
                and notifications[channel].get("reason") == "reserved_before_send"]
    report = Path(plan["archive"]).read_text(encoding="utf-8")
    report = report.split("\n---\n\n", 1)[-1].strip()

    def persist(channel, result):
        notifications[channel] = result
        save_status(plan["date"], status)

    if channels:
        notify.send_report(report, subject=f"AI 早报 · {plan['date']}", channels=channels, on_result=persist)
    else:
        print("No new channels to send; existing delivery records retained.")
    return status


def print_summary(status):
    lines = [f"## AI 早报 · {status['date']}", f"- Generation: {status.get('generation', 'unknown')}"]
    if status.get("archive"):
        lines.append(f"- Archive: {status['archive']}")
    for source in status.get("sources", []):
        lines.append(f"- Source {source['source']}: {source['status']}, {source['count']} items")
        if source["status"] in ("failed", "partial"):
            print(f"::warning::Source {source['source']}: {source['status']} ({source['count']} items)")
    for channel, delivery in status.get("notifications", {}).items():
        lines.append(f"- {channel}: {delivery['status']}")
        if delivery["status"] != "sent":
            print(f"::warning::Notification {channel}: {delivery['status']}; do not blindly replay")
    text = "\n".join(lines) + "\n"
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
            output.write(text)
