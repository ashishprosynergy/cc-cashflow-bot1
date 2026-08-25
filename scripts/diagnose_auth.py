#!/usr/bin/env python3
"""
Throwaway diagnostic: work out which form of the Double credentials is accepted.

Tries the values as stored, with any "Firm Name-" prefix stripped, mixed, and
swapped. Prints only lengths and pass/fail - never the credential values.
Delete this file (and diagnose-auth.yml) once the answer is known.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://api.doublehq.com/oauth/token"


def tail(s):
    """The part after the last hyphen - i.e. drop a 'Firm Name-' prefix."""
    return s.rsplit("-", 1)[-1]


def try_pair(label, client_id, client_secret):
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            got = json.loads(resp.read().decode())
        if got.get("access_token"):
            print(f"SUCCESS  [{label}]  -> got an access_token")
            return True
        print(f"odd      [{label}]  HTTP 200 but no access_token: {list(got)}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:180].replace("\n", " ")
        print(f"fail     [{label}]  HTTP {e.code}  {detail}")
    except Exception as e:
        print(f"fail     [{label}]  {type(e).__name__}: {e}")
    return False


def main():
    raw_id = os.environ.get("DOUBLE_CLIENT_ID", "")
    raw_secret = os.environ.get("DOUBLE_CLIENT_SECRET", "")

    if not raw_id or not raw_secret:
        print("One or both secrets are EMPTY in GitHub.")
        print(f"  DOUBLE_CLIENT_ID set: {bool(raw_id)}")
        print(f"  DOUBLE_CLIENT_SECRET set: {bool(raw_secret)}")
        return

    cid = raw_id.strip()
    sec = raw_secret.strip()

    print("=== shape (no values printed) ===")
    print(f"id     : raw_len={len(raw_id)} stripped_len={len(cid)} tail_len={len(tail(cid))}")
    print(f"secret : raw_len={len(raw_secret)} stripped_len={len(sec)} tail_len={len(tail(sec))}")
    if len(raw_id) != len(cid) or len(raw_secret) != len(sec):
        print("!! whitespace found - a stray space or newline is inside one of the secrets")
    print()

    candidates = [
        ("1 as-is", cid, sec),
        ("2 both hex-only", tail(cid), tail(sec)),
        ("3 id as-is, secret hex-only", cid, tail(sec)),
        ("4 id hex-only, secret as-is", tail(cid), sec),
        ("5 swapped as-is", sec, cid),
        ("6 swapped hex-only", tail(sec), tail(cid)),
    ]

    print("=== attempts ===")
    winner = None
    for label, client_id, client_secret in candidates:
        if try_pair(label, client_id, client_secret):
            winner = label
            break

    print()
    if winner:
        print(f"=== ANSWER: permutation {winner} works ===")
    else:
        print("=== ANSWER: none worked - the credential pair itself is not valid ===")
        print("Next step is Double support: API access may not be enabled for the practice,")
        print("or the pair has been superseded by a later regeneration.")


if __name__ == "__main__":
    main()
