#!/usr/bin/env python3
"""
Diagnostic v2: try every AUTHENTICATION METHOD for Double's token endpoint.

v1 established that the credentials are 32-hex, whitespace-free, and rejected
with invalid_client in both orderings when sent as form-body parameters.

invalid_client is the OAuth2 error for failed *client authentication*, and the
most common cause is that the server wants HTTP Basic auth rather than body
parameters. This tries both, plus JSON encoding, plus swapped ordering.

Prints only lengths and pass/fail - never the credential values.
"""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://api.doublehq.com/oauth/token"


def attempt(label, data, headers):
    req = urllib.request.Request(
        TOKEN_URL, data=data, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
        try:
            got = json.loads(raw)
        except ValueError:
            print(f"odd      [{label}]  HTTP 200 non-JSON: {raw[:120]}")
            return False
        if got.get("access_token"):
            print(f"SUCCESS  [{label}]  -> access_token received")
            return True
        print(f"odd      [{label}]  HTTP 200 but no access_token: {list(got)}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200].replace("\n", " ")
        print(f"fail     [{label}]  HTTP {e.code}  {detail}")
    except Exception as e:
        print(f"fail     [{label}]  {type(e).__name__}: {e}")
    return False


def basic(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


FORM = "application/x-www-form-urlencoded"
JSON_CT = "application/json"


def main():
    cid = os.environ.get("DOUBLE_CLIENT_ID", "").strip()
    sec = os.environ.get("DOUBLE_CLIENT_SECRET", "").strip()

    if not cid or not sec:
        print("One or both secrets are EMPTY in GitHub.")
        return

    print(f"id_len={len(cid)}  secret_len={len(sec)}")
    print()
    print("=== attempts ===")

    tests = [
        # A: what we have been doing all along - credentials in the form body.
        (
            "A form body (baseline)",
            urllib.parse.urlencode(
                {
                    "grant_type": "client_credentials",
                    "client_id": cid,
                    "client_secret": sec,
                }
            ).encode(),
            {"Content-Type": FORM},
        ),
        # B: the RFC-preferred way - Basic auth header, grant_type only in body.
        (
            "B basic auth header",
            urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
            {"Content-Type": FORM, "Authorization": basic(cid, sec)},
        ),
        # C: belt and braces - Basic header AND body credentials.
        (
            "C basic header + body creds",
            urllib.parse.urlencode(
                {
                    "grant_type": "client_credentials",
                    "client_id": cid,
                    "client_secret": sec,
                }
            ).encode(),
            {"Content-Type": FORM, "Authorization": basic(cid, sec)},
        ),
        # D: Basic auth with the pair reversed, in case of a labelling mix-up.
        (
            "D basic auth swapped",
            urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
            {"Content-Type": FORM, "Authorization": basic(sec, cid)},
        ),
        # E: JSON body instead of form encoding.
        (
            "E json body",
            json.dumps(
                {
                    "grant_type": "client_credentials",
                    "client_id": cid,
                    "client_secret": sec,
                }
            ).encode(),
            {"Content-Type": JSON_CT},
        ),
        # F: JSON body plus Basic header.
        (
            "F json body + basic header",
            json.dumps({"grant_type": "client_credentials"}).encode(),
            {"Content-Type": JSON_CT, "Authorization": basic(cid, sec)},
        ),
    ]

    winner = None
    for label, data, headers in tests:
        if attempt(label, data, headers):
            winner = label
            break

    print()
    if winner:
        print(f"=== ANSWER: {winner} works - full automation is unblocked ===")
    else:
        print("=== ANSWER: every auth method rejected the credential ===")
        print("This is no longer something the code can fix. The credential is not")
        print("active on Double's side - contact help@doublehq.com.")


if __name__ == "__main__":
    main()
