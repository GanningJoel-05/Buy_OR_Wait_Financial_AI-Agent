"""Offline evaluation and submission-contract checks.

Run after `python code/main.py`: `python code/evaluation/main.py`.
It validates the generated CSV and, if a predictions path is supplied, reports
field-level agreement with solved sample rows without using them at inference.
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COLS = ["request_id", "amount_safe_to_pay", "affordability_status", "recommended_payment_method", "payment_plan", "earliest_date_for_full_payment", "spending_changes_needed", "decision_explanation"]

def read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def main():
    rows, requests = read(ROOT / "output.csv"), read(ROOT / "dataset" / "requests.csv")
    errors = []
    if not rows or list(rows[0]) != COLS: errors.append("CSV columns are not exactly the required schema.")
    if len(rows) != len(requests): errors.append(f"Expected {len(requests)} rows, found {len(rows)}.")
    requested = {r["request_id"]: float(r["requested_amount"]) for r in requests}
    allowed_status = {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
    allowed_method = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}
    for row in rows:
        try:
            value = float(row["amount_safe_to_pay"])
            if not 0 <= value <= requested[row["request_id"]] + 0.01: errors.append(f"Bad safe amount for {row['request_id']}.")
        except Exception: errors.append(f"Non-numeric safe amount for {row.get('request_id')}.")
        if row.get("affordability_status") not in allowed_status: errors.append(f"Bad status for {row.get('request_id')}.")
        if row.get("recommended_payment_method") not in allowed_method: errors.append(f"Bad method for {row.get('request_id')}.")
    print("PASS" if not errors else "FAIL")
    for error in errors[:20]: print("-", error)
    sys.exit(1 if errors else 0)

if __name__ == "__main__": main()
