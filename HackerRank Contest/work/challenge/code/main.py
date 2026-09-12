"""Deterministic Buy-or-Wait financial planning agent.

Run from the repository root: python code/main.py
No API key is required.  The program reconstructs a conservative cash-flow
forecast, tests each eligible payment option, and verifies every selected plan.
"""
from __future__ import annotations

import calendar
import csv
import itertools
import re
import statistics
import subprocess
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "dataset"
OUT_COLUMNS = ["request_id", "amount_safe_to_pay", "affordability_status",
               "recommended_payment_method", "payment_plan",
               "earliest_date_for_full_payment", "spending_changes_needed",
               "decision_explanation"]
ZERO = Decimal("0")
BILL_CATEGORIES = {
    "salary", "rent", "housing", "utilities", "debt_repayment", "insurance",
    "education", "childcare", "transport", "groceries", "healthcare",
    "music_subscription", "streaming", "cloud_storage", "delivery_membership",
    "gym", "phone", "internet", "family_support",
}


def read_csv(name: str) -> list[dict[str, str]]:
    with (DATA / name).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def money(value: str | Decimal) -> Decimal:
    if value is None or str(value).strip() == "":
        return ZERO
    return Decimal(str(value).replace(",", "").strip())


def d(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def fmt_safe(amount: Decimal) -> str:
    """Safe-to-pay amounts drop needless trailing zeros."""
    q = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = format(q, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def fmt_plan(amount: Decimal) -> str:
    """Payment-plan amounts use two decimals whenever the value is not whole."""
    q = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if q == q.to_integral_value():
        return format(q.quantize(Decimal("1")), "f")
    return format(q, "f")


def grouped(amount: Decimal) -> str:
    q = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if q == q.to_integral_value():
        return f"{int(q):,}"
    return f"{q:,.2f}"


def split(value: str) -> set[str]:
    return {x.strip() for x in (value or "").split("|") if x.strip()}


def occupied(dates: set[date], day: date, window: int = 2) -> bool:
    return any(abs((day - other).days) <= window for other in dates)


def add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def step_date(day: date, interval: int) -> date:
    if interval >= 27:
        return add_months(day, 1)
    if interval <= 9:
        return day + timedelta(days=7)
    if interval <= 16:
        return day + timedelta(days=14)
    return day + timedelta(days=interval)


def first_on_or_after(last: date, interval: int, start: date) -> date:
    nxt = step_date(last, interval)
    while nxt < start:
        nxt = step_date(nxt, interval)
    return nxt


class Agent:
    def __init__(self) -> None:
        self.profiles = {r["user_id"]: r for r in read_csv("financial_profiles.csv")}
        self.events = read_csv("financial_events.csv")
        self.options = read_csv("request_payment_options.csv")
        self.messages = read_csv("messages.csv")
        self.images = read_csv("images.csv")
        self.ocr_cache: dict[str, Decimal | None] = {}
        self.rates = {(r["rate_date"], r["from_currency"], r["to_currency"]): money(r["rate"])
                      for r in read_csv("exchange_rates.csv")}
        self.by_user: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.by_request_options: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.event_by_id = {e["event_id"]: e for e in self.events}
        for event in self.events:
            self.by_user[event["user_id"]].append(event)
        for option in self.options:
            self.by_request_options[option["request_id"]].append(option)

    def converted_amount(self, event: dict[str, str], home: str) -> Decimal | None:
        raw = event.get("amount", "")
        if not raw.strip():
            return self.ocr_amount(event, home)
        amount = money(raw)
        currency = event.get("currency", home) or home
        if currency == home:
            return amount
        when = event.get("settlement_date") or event.get("event_date")
        rate = self.rates.get((when, currency, home))
        return amount * rate if rate is not None else None

    def ocr_amount(self, event: dict[str, str], home: str) -> Decimal | None:
        event_id = event["event_id"]
        if event_id in self.ocr_cache:
            return self.ocr_cache[event_id]
        matches = [x for x in self.images if x.get("related_event_id") == event_id]
        if not matches:
            self.ocr_cache[event_id] = None
            return None
        image = DATA / "media" / "images" / f"{matches[0]['image_id']}.png"
        try:
            text = subprocess.run(["tesseract", str(image), "stdout"], capture_output=True,
                                  text=True, timeout=20, check=False).stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self.ocr_cache[event_id] = None
            return None
        code = re.escape(event.get("currency") or home)
        found = (re.search(rf"{code}\s*([0-9][0-9,]*(?:\.\d{{1,2}})?)", text, re.I)
                 or re.search(rf"([0-9][0-9,]*(?:\.\d{{1,2}})?)\s*{code}", text, re.I)
                 or re.search(r"(?:total|amount|net|due)[^\d]{0,12}([0-9][0-9,]*(?:\.\d{1,2})?)", text, re.I))
        self.ocr_cache[event_id] = money(found.group(1)) if found else None
        return self.ocr_cache[event_id]

    def parse_messages(self, user_id: str, as_of: date, home: str) -> dict[str, object]:
        """Turn untrusted messages into dated cash-flow amendments, never into rule overrides."""
        effects: dict[str, object] = {
            "stop_salary": False,
            "salary_amount": None,
            "salary_from": None,
            "salary_shift_to": None,
            "one_cycle_salary": None,
            "one_time_credit": None,
            "rent_multiplier": Decimal("1"),
            "invoice_credits": [],
            "ignore_pending_platform": False,
            "internal_transfer": False,
        }
        amount_re = re.compile(
            r"\b(IDR|INR|ZAR|USD|EUR)\s*([0-9][0-9,]*(?:\.\d{1,2})?)", re.I)

        def convert(code: str, value: Decimal, when: date) -> Decimal:
            code = code.upper()
            if code == home:
                return value
            rate = self.rates.get((when.isoformat(), code, home))
            return value * rate if rate is not None else value

        for message in self.messages:
            if message.get("user_id") != user_id or not message.get("sent_at"):
                continue
            sent = d(message["sent_at"])
            if sent > as_of:
                continue
            text = message.get("message_text", "")
            dates = [d(x) for x in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)]
            amounts = [(m.group(1).upper(), money(m.group(2))) for m in amount_re.finditer(text)]
            effective = dates[-1] if dates else sent

            if re.search(r"transfer between your two accounts|kedua akun|same account holder", text, re.I):
                effects["internal_transfer"] = True
            if re.search(r"payout is still pending|masih tertunda|app can change|belum dapat ditarik", text, re.I):
                effects["ignore_pending_platform"] = True
            if re.search(r"increases monthly rent by 12%|naik 12%|increases monthly rent", text, re.I):
                effects["rent_multiplier"] = Decimal("1.12")
            if re.search(r"invoice payment of|pembayaran faktur sebesar|approved an invoice", text, re.I) and amounts:
                when = dates[-1] if dates else sent
                effects["invoice_credits"].append((when, convert(amounts[0][0], amounts[0][1], when)))
            if re.search(r"seasonal contract has ended|employment has ended|no regular salary|no off-season income|kontrak musiman", text, re.I):
                effects["stop_salary"] = True
            if re.search(r"remaining confirmed monthly salary|sisa gaji bulanan", text, re.I) and amounts:
                effects["stop_salary"] = False
                effects["salary_amount"] = convert(amounts[0][0], amounts[0][1], effective)
                effects["salary_from"] = effective
            if re.search(r"first salary|gaji pertama", text, re.I) and amounts:
                when = dates[-1] if dates else as_of
                effects["salary_amount"] = convert(amounts[0][0], amounts[0][1], when)
                effects["salary_from"] = when
                effects["salary_shift_to"] = when
            if re.search(r"now expected on|confirmed credit date|dikonfirmasi untuk|confirmed for", text, re.I) and dates:
                effects["salary_shift_to"] = dates[-1]
            if re.search(r"resumes on", text, re.I) and amounts and dates:
                effects["salary_amount"] = convert(amounts[0][0], amounts[0][1], dates[0])
                effects["salary_from"] = dates[0]
                effects["salary_shift_to"] = dates[0]
            if re.search(r"increased to|naik menjadi|has increased to", text, re.I) and amounts:
                effects["salary_amount"] = convert(amounts[0][0], amounts[0][1], effective)
                effects["salary_from"] = effective
            if re.search(r"temporary monthly pay|gaji bulanan sementara|next salary is reduced|gaji berikutnya sudah dikurangi|reduced to", text, re.I) and amounts:
                effects["one_cycle_salary"] = convert(amounts[0][0], amounts[0][1], effective)
            if re.search(r"confirmed base salary|gaji pokok yang dikonfirmasi|confirmed salary is", text, re.I) and amounts:
                # Ignore unapproved commission / bonus amounts that appear later in the same note.
                effects["salary_amount"] = convert(amounts[0][0], amounts[0][1], effective)
                effects["salary_from"] = effects["salary_from"] or effective
            if re.search(r"regular salary for the next payroll|gaji rutin Anda untuk penggajian berikutnya", text, re.I) and amounts:
                effects["salary_amount"] = convert(amounts[0][0], amounts[0][1], effective)
                effects["salary_from"] = effective
                if len(amounts) > 1 and re.search(r"arrears|tunggakan|one-time|satu kali", text, re.I):
                    effects["one_time_credit"] = (effective, convert(amounts[1][0], amounts[1][1], effective))
        return effects

    def relevant_events(self, user_id: str, as_of: date, home: str) -> list[dict[str, object]]:
        raw = self.by_user[user_id]
        cancelled_links = {e.get("linked_event_id") for e in raw
                           if e.get("status") in {"cancelled", "failed"} and e.get("linked_event_id")}
        result = []
        for e in raw:
            if e.get("event_id") in cancelled_links or e.get("status") in {"cancelled", "failed", "unrealized"}:
                continue
            amount = self.converted_amount(e, home)
            if amount is None:
                continue
            settle = d(e.get("settlement_date") or e["event_date"])
            if e.get("status") == "pending" and e.get("direction") == "credit":
                continue
            if e.get("event_type") == "investment_valuation":
                continue
            desc = (e.get("description") or "").lower()
            if "prize" in desc and e.get("direction") == "credit" and e.get("status") != "settled":
                continue
            result.append({"id": e["event_id"], "date": settle, "amount": amount,
                           "direction": e["direction"], "category": e.get("category", ""),
                           "description": e.get("description", ""), "status": e.get("status"),
                           "flexibility": e.get("flexibility", "fixed"),
                           "event_type": e.get("event_type", ""),
                           "minimum": money(e.get("minimum_allowed_amount", ""))})
        return result

    def forecast(self, user_id: str, start: date, days: int,
                 changes: list[tuple[str, Decimal, str, Decimal]] | None = None) -> dict[date, Decimal]:
        profile = self.profiles[user_id]
        home = profile["home_currency"]
        events = self.relevant_events(user_id, start, home)
        notes = self.parse_messages(user_id, start, home)
        end = start + timedelta(days=days)
        flows: dict[date, Decimal] = defaultdict(lambda: ZERO)
        taken: dict[tuple[str, str], set[date]] = defaultdict(set)

        def add_flow(day: date, category: str, direction: str, amount: Decimal) -> None:
            if not (start <= day <= end):
                return
            signed = amount if direction == "credit" else -amount
            flows[day] += signed
            taken[(category, direction)].add(day)

        for e in events:
            eday = e["date"]
            if notes["internal_transfer"] and "transfer" in str(e["description"]).lower() and start <= eday <= end:
                continue
            if notes["ignore_pending_platform"] and e["status"] == "pending" and e["direction"] == "credit":
                continue
            # Opening balance already includes settled history; only reserve unpaid future cash.
            if start <= eday <= end and e["status"] in {"pending", "scheduled"}:
                add_flow(eday, str(e["category"]), str(e["direction"]), e["amount"])

        groups: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
        for e in events:
            if e["date"] < start and e["status"] == "settled":
                groups[(str(e["category"]), str(e["direction"]))].append(e)

        salary_projected = False
        for (category, direction), hist in groups.items():
            hist.sort(key=lambda x: x["date"])
            if direction == "credit" and category != "salary":
                continue
            lookback = 180 if category in BILL_CATEGORIES else 120
            recent = [x for x in hist if x["date"] >= start - timedelta(days=lookback)]
            if category == "salary":
                if notes["stop_salary"]:
                    continue
                recent = [x for x in recent if not re.search(
                    r"prorated|first salary|bonus|arrears|commission|prize|promotion",
                    str(x["description"]), re.I)]
                if recent and re.search(r"final employer|final payroll|final settlement",
                                        str(recent[-1]["description"]), re.I):
                    continue
                streams: dict[int, list] = defaultdict(list)
                for row in recent:
                    streams[row["date"].day].append(row)
                for _day, stream in streams.items():
                    stream.sort(key=lambda x: x["date"])
                    if len(stream) < 2:
                        continue
                    if any(re.search(r"platform|freelance|driver|task marketplace|app earnings|gig",
                                     str(x["description"]), re.I) for x in stream):
                        continue
                    if not any(re.search(r"payroll|salary|wage|gaji|household",
                                         str(x["description"]), re.I) for x in stream):
                        continue
                    sample_amts = [float(x["amount"]) for x in stream]
                    med = statistics.median(sample_amts)
                    if not med:
                        continue
                    inliers = [x for x in stream if abs(float(x["amount"]) - med) / med <= 0.2]
                    if len(inliers) < 2:
                        continue
                    amount = Decimal(str(statistics.median([float(x["amount"]) for x in inliers])))
                    if notes["salary_amount"] is not None:
                        amount = notes["salary_amount"]  # type: ignore[assignment]
                    last = inliers[-1]["date"]
                    nxt = first_on_or_after(last, 30, start)
                    if notes["salary_shift_to"] and last < notes["salary_shift_to"]:
                        nxt = notes["salary_shift_to"]  # type: ignore[assignment]
                    while nxt <= end:
                        if not occupied(taken[("salary", "credit")], nxt):
                            pay_amount = amount
                            if notes["one_cycle_salary"] is not None and nxt <= start + timedelta(days=40):
                                pay_amount = notes["one_cycle_salary"]  # type: ignore[assignment]
                            add_flow(nxt, "salary", "credit", pay_amount)
                            salary_projected = True
                        nxt = step_date(nxt, 30)
                continue
            need = 2 if category in BILL_CATEGORIES else 3
            if len(recent) < need:
                continue
            dates = [x["date"] for x in recent]
            gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if 5 <= (b - a).days <= 40]
            if len(gaps) < 1:
                continue
            interval = int(round(statistics.median(gaps)))
            if interval < 6 or interval > 40:
                continue
            sample = recent[-min(8, len(recent)):]
            amounts = [float(x["amount"]) for x in sample]
            amount = Decimal(str(statistics.median(amounts)))
            if category in {"rent", "housing"} and notes["rent_multiplier"] != 1:
                amount *= notes["rent_multiplier"]  # type: ignore[operator]
            if category == "salary" and notes["salary_amount"] is not None:
                amount = notes["salary_amount"]  # type: ignore[assignment]
            last = dates[-1]
            nxt = first_on_or_after(last, interval, start)
            if category == "salary" and notes["salary_shift_to"] and last < notes["salary_shift_to"]:
                nxt = notes["salary_shift_to"]  # type: ignore[assignment]
            while nxt <= end:
                if not occupied(taken[(category, direction)], nxt):
                    pay_amount = amount
                    if category == "salary" and notes["one_cycle_salary"] is not None and nxt <= start + timedelta(days=40):
                        pay_amount = notes["one_cycle_salary"]  # type: ignore[assignment]
                    add_flow(nxt, category, direction, pay_amount)
                    if category == "salary":
                        salary_projected = True
                nxt = step_date(nxt, interval)

        if not notes["stop_salary"]:
            scheduled_salary = [e for e in events if e["category"] == "salary" and e["direction"] == "credit"
                                and e["status"] == "scheduled" and start <= e["date"] <= end]
            seed = None
            if scheduled_salary:
                seed = max(scheduled_salary, key=lambda x: x["date"])
            elif notes["salary_shift_to"] or notes["salary_from"]:
                seed_date = notes["salary_shift_to"] or notes["salary_from"]
                seed_amt = notes["salary_amount"] or notes["one_cycle_salary"]
                if seed_date and seed_amt:
                    seed = {"date": seed_date, "amount": seed_amt}
            if seed is not None:
                amount = notes["salary_amount"] or seed["amount"]
                nxt = seed["date"] if seed["date"] >= start else add_months(seed["date"], 1)
                if salary_projected:
                    nxt = add_months(seed["date"], 1)
                while nxt <= end:
                    if not occupied(taken[("salary", "credit")], nxt):
                        add_flow(nxt, "salary", "credit", amount)
                        salary_projected = True
                    nxt = add_months(nxt, 1)

        if notes["one_time_credit"]:
            when, extra = notes["one_time_credit"]  # type: ignore[misc]
            if start <= when <= end:
                flows[when] += extra
        for when, extra in notes["invoice_credits"]:  # type: ignore[misc]
            if start <= when <= end:
                flows[when] += extra

        for event_id, amount, action, floor in changes or []:
            source = next((e for e in events if e["id"] == event_id), None)
            if not source:
                continue
            saving = amount if action == "stop" else max(ZERO, amount - floor)
            peers = sorted([e["date"] for e in events if e["category"] == source["category"]
                            and e["direction"] == "debit" and e["date"] < start])
            gaps = [(b - a).days for a, b in zip(peers, peers[1:]) if 5 <= (b - a).days <= 40]
            interval = int(round(statistics.median(gaps))) if gaps else 30
            nxt = source["date"] + timedelta(days=interval)
            while nxt < start:
                nxt += timedelta(days=interval)
            while nxt <= end:
                flows[nxt] += saving
                nxt += timedelta(days=interval)
        return flows

    def balances(self, user_id: str, start: date, payments: list[tuple[date, Decimal]],
                 changes: list[tuple[str, Decimal, str, Decimal]] | None = None) -> tuple[Decimal, Decimal]:
        profile = self.profiles[user_id]
        initial, minimum = money(profile["current_available_balance"]), money(profile["minimum_balance_to_keep"])
        flows = self.forecast(user_id, start, 90, changes)
        for pay_day, amount in payments:
            if start <= pay_day <= start + timedelta(days=90):
                flows[pay_day] -= amount
        balance, low = initial, initial
        for n in range(91):
            balance += flows[start + timedelta(days=n)]
            low = min(low, balance)
        return low, minimum

    def safe(self, user_id: str, start: date, payments: list[tuple[date, Decimal]],
             changes: list[tuple[str, Decimal, str, Decimal]] | None = None) -> bool:
        low, minimum = self.balances(user_id, start, payments, changes)
        return low + Decimal("0.005") >= minimum

    def amount_safe_today(self, user_id: str, request_date: date, requested: Decimal) -> Decimal:
        low, minimum = self.balances(user_id, request_date, [])
        return max(ZERO, min(requested, (low - minimum).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)))

    def first_safe_full_date(self, user_id: str, request_date: date, requested: Decimal) -> date | None:
        for offset in range(91):
            candidate = request_date + timedelta(days=offset)
            if self.safe(user_id, request_date, [(candidate, requested)]):
                return candidate
        return None

    def eligible_options(self, request: dict[str, str], profile: dict[str, str]
                         ) -> list[tuple[dict[str, str], list[tuple[date, Decimal]]]]:
        considers = split(profile["payment_methods_user_will_consider"])
        permitted_months = int(profile["max_installment_months"]) if profile.get("max_installment_months") else 0
        answer = []
        for opt in self.by_request_options[request["request_id"]]:
            method = opt["payment_method"]
            if method not in considers:
                continue
            count = int(opt["number_of_payments"])
            freq = int(opt["payment_frequency_days"] or 0)
            if method == "installments":
                if not permitted_months:
                    continue
                span_days = freq * max(count - 1, 0)
                if span_days > permitted_months * 31:
                    continue
            first, amount = d(opt["first_payment_date"]), money(opt["payment_amount"])
            plan = [(first + timedelta(days=freq * i), amount) for i in range(count)]
            answer.append((opt, plan))
        return answer

    def flexible_changes(self, user_id: str, request_date: date) -> list[tuple[str, Decimal, str, Decimal, str]]:
        profile = self.profiles[user_id]
        stop_cats = split(profile["expense_categories_user_is_willing_to_stop"])
        reduce_cats = split(profile["expense_categories_user_is_willing_to_reduce"])
        events = self.relevant_events(user_id, request_date, profile["home_currency"])
        choices = []
        for e in events:
            if e["date"] >= request_date or e["direction"] != "debit":
                continue
            cat, flex = str(e["category"]), str(e["flexibility"])
            if cat in stop_cats and flex in {"stoppable", "reducible_or_stoppable"}:
                choices.append((str(e["id"]), e["amount"], "stop", ZERO, e["date"], cat, str(e["description"])))
            elif cat in reduce_cats and flex in {"reducible", "reducible_or_stoppable"}:
                choices.append((str(e["id"]), e["amount"], "reduce", e["minimum"], e["date"], cat, str(e["description"])))
        newest = {}
        for item in choices:
            key = (item[5], item[2])
            if key not in newest or item[4] > newest[key][4]:
                newest[key] = item
        ranked = sorted(newest.values(), key=lambda x: x[1], reverse=True)[:6]
        return [(x[0], x[1], x[2], x[3], x[6]) for x in ranked]

    def plan_changes(self, user_id: str, request_date: date, payments: list[tuple[date, Decimal]]):
        if self.safe(user_id, request_date, payments):
            return []
        candidates = self.flexible_changes(user_id, request_date)
        for take in range(1, min(3, len(candidates)) + 1):
            for picked in itertools.combinations(candidates, take):
                payload = [(x[0], x[1], x[2], x[3]) for x in picked]
                if self.safe(user_id, request_date, payments, payload):
                    return list(picked)
        return None

    def change_phrase(self, changes) -> str:
        parts = []
        for item in changes:
            desc = item[4][0].lower() + item[4][1:] if item[4] else "flexible expense"
            if item[2] == "stop":
                parts.append(f"stop the {desc}")
            else:
                parts.append(f"reduce the {desc} to {grouped(item[3])}")
        if not parts:
            return ""
        if len(parts) == 1:
            text = parts[0]
        else:
            text = ", ".join(parts[:-1]) + " and " + parts[-1]
        return text[0].upper() + text[1:]

    def decide(self, request: dict[str, str]) -> dict[str, str]:
        profile = self.profiles[request["user_id"]]
        home, today = profile["home_currency"], d(request["request_date"])
        requested, deadline = money(request["requested_amount"]), d(request["desired_completion_date"])
        minimum = money(profile["minimum_balance_to_keep"])
        safe_today = self.amount_safe_today(request["user_id"], today, requested)
        earliest = self.first_safe_full_date(request["user_id"], today, requested)
        eligible = self.eligible_options(request, profile)
        considers = split(profile["payment_methods_user_will_consider"])

        candidates = []
        for opt, plan in eligible:
            if plan[-1][0] > deadline:
                continue
            if self.safe(request["user_id"], today, plan):
                total = sum((x[1] for x in plan), ZERO)
                candidates.append((0, 0, total, plan[0][0], len(plan), opt["payment_option_id"], opt, plan, []))
            else:
                changed = self.plan_changes(request["user_id"], today, plan)
                if changed:
                    total = sum((x[1] for x in plan), ZERO)
                    candidates.append((0, 1, total, plan[0][0], len(plan), opt["payment_option_id"], opt, plan, changed))
        if (request["allows_partial_payment"].lower() == "true" and "partial_payment" in considers
                and ZERO < safe_today < requested and earliest and earliest <= deadline):
            partial = [(today, safe_today), (earliest, requested - safe_today)]
            if self.safe(request["user_id"], today, partial):
                candidates.append((0, 0, requested, today, 2, "partial",
                                   {"payment_method": "partial_payment", "payment_option_id": "partial"},
                                   partial, []))

        if candidates:
            picked = sorted(candidates, key=lambda x: x[:6])[0]
            opt, plan, changes = picked[6], picked[7], picked[8]
            method = opt["payment_method"]
            no_changes = not changes
            is_now = method == "full_payment" and plan[0][0] == today and no_changes
            status = "affordable_now" if is_now else "affordable_with_plan"
            plan_text = "|".join(f"{day.isoformat()}:{fmt_plan(amount)}" for day, amount in plan)
            change_text = "|".join(
                f"stop:{x[0]}" if x[2] == "stop" else f"reduce_to:{x[0]}:{fmt_plan(x[3])}" for x in changes
            ) or "none"
            phrase = self.change_phrase(changes)
            if method == "full_payment":
                explanation = f"Pay {home} {grouped(requested)} today. This leaves at least {home} {grouped(minimum)} available over the next 90 days."
            elif method == "partial_payment":
                explanation = (f"Pay {home} {grouped(plan[0][1])} today and the remaining {home} {grouped(plan[1][1])} "
                               f"on {plan[1][0].strftime('%d %B %Y')}. This completes the full request and keeps the "
                               f"{home} {grouped(minimum)} minimum protected.")
            else:
                explanation = (f"Use {len(plan)} installments of {home} {grouped(plan[0][1])}, starting "
                               f"{plan[0][0].strftime('%d %B %Y')}. This leaves at least {home} {grouped(minimum)} available.")
            if phrase:
                rest = explanation[0].lower() + explanation[1:]
                explanation = f"{phrase}, then {rest}"
            earliest_out = today.isoformat() if is_now else (earliest.isoformat() if earliest else "")
            return {"request_id": request["request_id"], "amount_safe_to_pay": fmt_safe(safe_today),
                    "affordability_status": status, "recommended_payment_method": method,
                    "payment_plan": plan_text, "earliest_date_for_full_payment": earliest_out,
                    "spending_changes_needed": change_text, "decision_explanation": explanation}

        if earliest and "full_payment" in considers:
            when = earliest
            if when > deadline:
                status = "affordable_later"
            else:
                status = "affordable_later"
            plan_text = f"{when.isoformat()}:{fmt_plan(requested)}"
            if when == deadline:
                explanation = (f"Pay {home} {grouped(requested)} in full on {when.strftime('%d %B %Y')}. "
                               f"Paying earlier would take the balance below the {home} {grouped(minimum)} minimum.")
            else:
                explanation = (f"Wait until {when.strftime('%d %B %Y')}, then pay {home} {grouped(requested)} in full. "
                               f"Paying sooner would put the {home} {grouped(minimum)} minimum at risk.")
            return {"request_id": request["request_id"], "amount_safe_to_pay": fmt_safe(safe_today),
                    "affordability_status": status, "recommended_payment_method": "wait",
                    "payment_plan": plan_text, "earliest_date_for_full_payment": when.isoformat(),
                    "spending_changes_needed": "none", "decision_explanation": explanation}

        if safe_today > ZERO:
            explanation = (f"Do not proceed with the {home} {grouped(requested)} request. Although "
                           f"{home} {grouped(safe_today)} is available today, the full amount cannot be completed safely within 90 days.")
        else:
            explanation = (f"Do not make this payment by {deadline.strftime('%d %B %Y')}. None of the available options "
                           f"keeps the {home} {grouped(minimum)} minimum protected.")
        return {"request_id": request["request_id"], "amount_safe_to_pay": fmt_safe(safe_today),
                "affordability_status": "not_affordable", "recommended_payment_method": "not_recommended",
                "payment_plan": "none", "earliest_date_for_full_payment": "", "spending_changes_needed": "none",
                "decision_explanation": explanation}


def main() -> None:
    agent = Agent()
    requests = read_csv("requests.csv")
    rows = [agent.decide(request) for request in requests]
    with (ROOT / "output.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} predictions to {ROOT / 'output.csv'}")


if __name__ == "__main__":
    main()
