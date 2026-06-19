"""Read + summarize the human verification feedback collected by tools/verify_app.py.

Feedback lives in tests/feedback/<clip>.json as:
  {"clip": key, "frames": {"<n>": {"<weapon>": {"status": "correct"|"value"|"absent",
                                                 "value": <int, only for 'value'>}}}}

This prints what the human marked, focusing on CORRECTIONS (value/absent) — the cells where
the ground truth (or the detector) was wrong — so they can be folded back into the GT files.
Run: python tools/read_feedback.py
"""
import os
import sys
import json
import glob

FEEDBACK_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "feedback"))


def main():
    files = sorted(glob.glob(os.path.join(FEEDBACK_DIR, "*.json")))
    if not files:
        print("No feedback yet (tests/feedback/ empty). Run tools/verify_app.py and mark frames.")
        return
    for p in files:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        frames = data.get("frames", {})
        key = data.get("clip", os.path.basename(p))
        corrections = []
        confirmed = 0
        for n, cells in frames.items():
            for wp, e in cells.items():
                if e.get("status") == "correct":
                    confirmed += 1
                else:
                    corrections.append((int(n), wp, e))
        print(f"\n=== {key} ===")
        print(f"  cells marked: {sum(len(c) for c in frames.values())}  "
              f"(confirmed={confirmed}, corrections={len(corrections)})")
        for n, wp, e in sorted(corrections):
            if e["status"] == "value":
                print(f"    f{n:4d} {wp:5s} -> TRUE VALUE {e.get('value')}")
            else:
                print(f"    f{n:4d} {wp:5s} -> ROW ABSENT")


if __name__ == "__main__":
    main()
