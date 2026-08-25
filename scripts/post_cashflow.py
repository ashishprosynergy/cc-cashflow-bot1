#!/usr/bin/env python3
"""
Weekly C&C cashflow bridge: Double (doublehq.com) -> thechrisandclaudecf dashboard.

Pulls the balance sheet and P&L for THE CHRIS & CLAUDE CO out of Double's REST API,
assembles the /api/overhead payload, POSTs it, then verifies it landed.

Stdlib only - no pip install, so the GitHub Actions run stays fast and can't break
on a dependency update.

Env vars required:
  DOUBLE_CLIENT_ID      - from Double: Practice Settings > Practice Management > General
  DOUBLE_CLIENT_SECRET  - same place (shown once at generation)
Optional:
  DOUBLE_CLIENT_NUM     - Double's numeric client id (default 624571 = THE CHRIS & CLAUDE CO)
  DASHBOARD_BASE        - default https://thechrisandclaudecf.netlify.app
  DRY_RUN               - "1" to print the payload and skip the POST
"""

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DOUBLE_BASE = "https://api.doublehq.com"
DASHBOARD_BASE = os.environ.get(
    "DASHBOARD_BASE", "https://thechrisandclaudecf.netlify.app"
)
CLIENT_NUM = os.environ.get("DOUBLE_CLIENT_NUM", "624571")
DRY_RUN = os.environ.get("DRY_RUN") == "1"

# Balance sheet rows we need, by Double's stable metricsVariableId.
# (Fallback name match is used if an id ever changes.)
BS_BANK = ("BalanceSheet.QuickBooksCheckingAccount", "QuickBooks Checking Account")
BS_QBO_LOC = ("BalanceSheet.LINEOFCREDIT:QuickbooksLOC", "Quickbooks LOC")
BS_TRUIST = ("BalanceSheet.LINEOFCREDIT:SmallBusinessLine5998", "Small Business Line 5998")

# Class-column labels on the P&L-by-class report.
# Matched by prefix so a rename (e.g. "2.0 D/B Design" -> "2.0 D/B Sales & Design")
# does not silently break the mapping.
CLASS_BUILD_PREFIX = "3.0 D/B Build"
CLASS_DESIGN_PREFIX = "2.0 D/B"


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

def _request(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {e.code} from {url}\n{body[:2000]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Could not reach {url}: {e.reason}")


def get_token():
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": os.environ["DOUBLE_CLIENT_ID"],
            "client_secret": os.environ["DOUBLE_CLIENT_SECRET"],
        }
    ).encode()
    _, text = _request(
        f"{DOUBLE_BASE}/oauth/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    tok = json.loads(text).get("access_token")
    if not tok:
        raise SystemExit(f"No access_token in Double token response: {text[:500]}")
    return tok


def report(token, kind, start=None, end=None, summarize=None):
    """kind: profit-and-loss | balance-sheet | cash-flow"""
    params = {}
    if start:
        params["startDate"] = start
    if end:
        params["endDate"] = end
    if summarize:
        params["summarizeBy"] = summarize
    url = (
        f"{DOUBLE_BASE}/api/clients/{CLIENT_NUM}/financial-reports/{kind}"
        f"?{urllib.parse.urlencode(params)}"
    )
    _, text = _request(url, headers={"Authorization": f"Bearer {token}"})
    return json.loads(text)


# --------------------------------------------------------------------------
# Report parsing
# --------------------------------------------------------------------------

def walk(nodes):
    for n in nodes or []:
        yield n
        yield from walk(n.get("children"))


def find_by_id(rep, metrics_id, name_fallback=None):
    """Find a row/section by metricsVariableId, falling back to exact name."""
    nodes = list(walk(rep.get("sections")))
    for n in nodes:
        if n.get("metricsVariableId") == metrics_id:
            return n
    if name_fallback:
        for n in nodes:
            if n.get("name") == name_fallback:
                return n
    return None


def find_by_group(rep, group):
    for n in walk(rep.get("sections")):
        if n.get("group") == group:
            return n
    return None


def scalar(node, label):
    """Pull the single 'total' off a summarizeBy=Total node."""
    if node is None:
        raise SystemExit(f"Could not locate '{label}' in the Double report.")
    val = node.get("total")
    if val is None:
        raise SystemExit(f"'{label}' has no total value.")
    return round(float(val), 2)


def class_column(rep, node, label_prefix, what):
    """
    Pull one class column's amount off a summarizeBy=Classes node.

    Columns and totals are positional, and Double repeats a parent's key on its
    'Total <parent>' column - so pair by INDEX, never by key lookup.
    """
    if node is None:
        raise SystemExit(f"Could not locate the Overhead section for {what}.")
    cols = rep.get("report", {}).get("columns", [])
    totals = node.get("totals", [])
    for i, col in enumerate(cols):
        lbl = (col.get("label") or "").strip()
        if lbl.startswith(label_prefix) and not lbl.startswith("Total"):
            if i < len(totals):
                return round(float(totals[i].get("amount") or 0), 2)
    raise SystemExit(
        f"No class column starting with '{label_prefix}' ({what}). "
        f"Columns seen: {[c.get('label') for c in cols]}"
    )


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

def periods(today):
    """Current month-to-date, and the last FULLY COMPLETED Mon-Sun week."""
    month_start = today.replace(day=1)
    # Most recent Sunday strictly before today.
    days_since_sun = (today.weekday() + 1) % 7  # Mon=1 ... Sun=0
    last_sunday = today - dt.timedelta(days=days_since_sun or 7)
    last_monday = last_sunday - dt.timedelta(days=6)
    return {
        "month_key": today.strftime("%Y-%m"),
        "month_start": month_start.isoformat(),
        "today": today.isoformat(),
        "week_start": last_monday.isoformat(),
        "week_end": last_sunday.isoformat(),
        "week_label": f"Week of {last_monday.strftime('%b')} {last_monday.day}",
    }


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    for var in ("DOUBLE_CLIENT_ID", "DOUBLE_CLIENT_SECRET"):
        if not os.environ.get(var):
            raise SystemExit(f"Missing required env var {var}")

    p = periods(dt.date.today())
    print(f"Month to date : {p['month_start']} -> {p['today']}  ({p['month_key']})")
    print(f"Last full week: {p['week_start']} -> {p['week_end']}  ({p['week_label']})")

    token = get_token()

    bs = report(token, "balance-sheet", end=p["today"], summarize="Total")
    pl_mtd = report(token, "profit-and-loss", p["month_start"], p["today"], "Total")
    pl_cls = report(token, "profit-and-loss", p["month_start"], p["today"], "Classes")
    pl_wk = report(token, "profit-and-loss", p["week_start"], p["week_end"], "Total")
    cf_wk = report(token, "cash-flow", p["week_start"], p["week_end"], "Total")

    # --- Balance sheet ---
    bank = scalar(find_by_id(bs, *BS_BANK), "operating checking balance")
    qbo_drawn = scalar(find_by_id(bs, *BS_QBO_LOC), "QBO line of credit")

    # Truist SBL == the Small Business Line. It can sit slightly negative (a credit
    # balance / overpayment), which is not a "drawn" amount - floor it at zero so the
    # dashboard never shows a negative draw.
    truist_raw = scalar(find_by_id(bs, *BS_TRUIST), "Truist small business line")
    truist_drawn = max(0.0, truist_raw)
    if truist_raw < 0:
        print(f"note: Small Business Line is {truist_raw} (credit balance) -> posting 0")

    # --- P&L month to date (whole company) ---
    m_revenue = scalar(find_by_group(pl_mtd, "Income"), "MTD income")
    m_jobcost = scalar(find_by_group(pl_mtd, "CostOfGoodsSold"), "MTD job cost")
    # Full operating expense: the whole Expenses block (overhead + personnel + farm),
    # deliberately NOT just the 2.0/3.0 classes.
    m_overhead = scalar(find_by_group(pl_mtd, "Expenses"), "MTD total opex")

    # --- P&L by class ---
    overhead_node = find_by_id(pl_cls, "ProfitLoss.Overhead", "Overhead")
    build = class_column(pl_cls, overhead_node, CLASS_BUILD_PREFIX, "build overhead")
    design = class_column(pl_cls, overhead_node, CLASS_DESIGN_PREFIX, "design overhead")

    # --- Last completed week ---
    w_revenue = scalar(find_by_group(pl_wk, "Income"), "weekly income")
    w_jobcost = scalar(find_by_group(pl_wk, "CostOfGoodsSold"), "weekly job cost")
    w_overhead = scalar(find_by_group(pl_wk, "Expenses"), "weekly opex")

    # Double's cash flow statement reports NET movement, not gross in/out. Derive:
    #   cashIn  = revenue adjusted for the change in A/R (i.e. revenue actually collected)
    #   cashOut = cashIn - net change in cash
    # so that (cashIn - cashOut) always reconciles to the real net movement.
    net_change = scalar(
        find_by_id(cf_wk, "CashFlow.Netcashincreaseforperiod", "Net cash increase for period"),
        "weekly net cash change",
    )
    ar_node = find_by_id(cf_wk, "CashFlow.AccountsReceivable", "Accounts Receivable")
    ar_adj = round(float(ar_node.get("total") or 0), 2) if ar_node else 0.0
    cash_in = round(w_revenue + ar_adj, 2)
    cash_out = round(cash_in - net_change, 2)

    payload = {
        "bankBalance": bank,
        "qboDrawn": qbo_drawn,
        "truistDrawn": truist_drawn,
        "build": build,
        "design": design,
        "monthly": {
            "m": p["month_key"],
            "revenue": m_revenue,
            "jobCost": m_jobcost,
            "overhead": m_overhead,
        },
        "weekly": {
            "label": p["week_label"],
            "revenue": w_revenue,
            "jobCost": w_jobcost,
            "overhead": w_overhead,
            "cashIn": cash_in,
            "cashOut": cash_out,
        },
    }

    print("\nPayload:\n" + json.dumps(payload, indent=2))

    if DRY_RUN:
        print("\nDRY_RUN=1 - not posting.")
        return

    # --- POST (a real JSON POST, so the dashboard records source=weekly) ---
    status, body = _request(
        f"{DASHBOARD_BASE}/api/overhead",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    print(f"\nPOST /api/overhead -> HTTP {status}\n{body[:1000]}")

    # --- Verify it landed ---
    _, verify_raw = _request(f"{DASHBOARD_BASE}/api/data")
    oh = json.loads(verify_raw).get("overhead") or {}

    problems = []
    if oh.get("source") != "weekly":
        problems.append(f'source is "{oh.get("source")}", expected "weekly"')
    for field in ("bankBalance", "qboDrawn", "truistDrawn"):
        if oh.get(field) is None:
            problems.append(f"{field} is null")
    monthly = oh.get("monthlyActuals") or {}
    if isinstance(monthly, dict) and p["month_key"] not in monthly:
        problems.append(f'monthlyActuals has no entry for {p["month_key"]}')

    if problems:
        raise SystemExit("Verification FAILED:\n  - " + "\n  - ".join(problems))

    print(
        f"\nVerified: source=weekly, balances non-null, "
        f"monthlyActuals has {p['month_key']}. Done."
    )


if __name__ == "__main__":
    sys.exit(main())
