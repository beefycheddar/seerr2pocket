#!/usr/bin/env python3
"""
seerr2pocket.py - copy users (name + email) from Seerr into Pocket ID.

One-time migration. Python 3.6+, standard library only (no pip installs).
https://github.com/beefycheddar/seerr2pocket

Dry run by default: it shows what it would change and changes nothing.
Add --apply to actually make the changes.

Usage:
  export SEERR_URL=http://seerr.local:5055
  export SEERR_API_KEY=...          # Seerr > Settings > General > API Key
  export POCKET_URL=https://id.example.com
  export POCKET_API_KEY=...         # Pocket ID > Settings > Admin > API Keys

  python3 seerr2pocket.py                          # preview
  python3 seerr2pocket.py --apply                  # create users
  python3 seerr2pocket.py --update-names           # preview name changes too
  python3 seerr2pocket.py --update-names --apply   # create + rename
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def http(method, url, headers, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


# ---------- Seerr ----------

def seerr_users(base, key):
    users, skip, take = [], 0, 100
    while True:
        status, data = http(
            "GET", f"{base}/api/v1/user?take={take}&skip={skip}",
            {"X-Api-Key": key},
        )
        if status != 200:
            sys.exit(f"Seerr user list failed ({status}): {data}")
        batch = data.get("results", [])
        users.extend(batch)
        if len(batch) < take:
            return users
        skip += take


# ---------- Pocket ID ----------

def pocket_existing(base, key):
    """Return (dict lowercase email -> user, set of lowercase usernames)."""
    emails, names, page = {}, set(), 1
    while True:
        status, data = http(
            "GET",
            f"{base}/api/users?pagination%5Bpage%5D={page}&pagination%5Blimit%5D=100",
            {"X-API-KEY": key},
        )
        if status != 200:
            sys.exit(f"Pocket ID user list failed ({status}): {data}")
        rows = data.get("data", []) if isinstance(data, dict) else data
        for u in rows:
            if u.get("email"):
                emails[u["email"].lower()] = u
            if u.get("username"):
                names.add(u["username"].lower())
        pag = data.get("pagination", {}) if isinstance(data, dict) else {}
        if not rows or page >= pag.get("totalPages", 1):
            return emails, names
        page += 1


def make_username(seed, taken):
    """Pocket ID usernames: lowercase letters, digits, _ . - ; must start/end alphanumeric."""
    u = re.sub(r"[^a-z0-9_.-]", "", seed.lower())
    u = re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", u)[:40] or "user"
    if len(u) < 2:
        u = f"{u}user"
    base, n = u, 2
    while u in taken:
        u = f"{base}{n}"
        n += 1
    taken.add(u)
    return u


def split_name(display):
    parts = display.strip().split(None, 1)
    first = parts[0] if parts else ""
    last = parts[1] if len(parts) > 1 else ""
    return first[:50], last[:50]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually make changes")
    ap.add_argument("--update-names", action="store_true",
                    help="also overwrite first/last/display name of users "
                         "already in Pocket ID (matched by email) with Seerr's")
    args = ap.parse_args()

    try:
        s_url = os.environ["SEERR_URL"].rstrip("/")
        s_key = os.environ["SEERR_API_KEY"]
        p_url = os.environ["POCKET_URL"].rstrip("/")
        p_key = os.environ["POCKET_API_KEY"]
    except KeyError as e:
        sys.exit(f"Missing environment variable {e}")

    src = seerr_users(s_url, s_key)
    have_emails, have_names = pocket_existing(p_url, p_key)
    print(f"Seerr users: {len(src)}   Pocket ID users: {len(have_emails)}\n")

    created = exists = updated = no_email = failed = 0
    for su in src:
        email = (su.get("email") or "").strip()
        display = (su.get("displayName") or su.get("username")
                   or su.get("jellyfinUsername") or su.get("plexUsername")
                   or email.split("@")[0]).strip()

        if not EMAIL_RE.match(email):
            # Jellyfin-imported Seerr users often have their username in the email field
            print(f"  SKIP  {display!r}: no real email (got {email!r})")
            no_email += 1
            continue
        first, last = split_name(display)

        existing = have_emails.get(email.lower())
        if existing:
            exists += 1
            if not args.update_names:
                print(f"  HAVE  {email}")
                continue
            if existing.get("ldapId"):
                print(f"  HAVE  {email}  (LDAP-managed, not editing)")
                continue
            new_first = first or existing.get("firstName") or ""
            new_display = display[:100] or existing.get("displayName") or ""
            old = (existing.get("firstName") or "", existing.get("lastName") or "",
                   existing.get("displayName") or "")
            new = (new_first, last, new_display)
            if old == new:
                print(f"  SAME  {email}")
                continue
            print(f"  NAME  {email}:  {old[0]!r} {old[1]!r} / {old[2]!r}"
                  f"  ->  {new[0]!r} {new[1]!r} / {new[2]!r}")
            if not args.apply:
                updated += 1
                continue
            # PUT replaces the whole record, so carry every other field over unchanged
            body = {
                "username": existing.get("username"),
                "email": existing.get("email"),
                "firstName": new_first,
                "lastName": last,
                "displayName": new_display,
                "isAdmin": existing.get("isAdmin", False),
            }
            for k in ("locale", "disabled"):
                if k in existing:
                    body[k] = existing[k]
            status, resp = http("PUT", f"{p_url}/api/users/{existing['id']}",
                                {"X-API-KEY": p_key}, body)
            if status in (200, 201):
                updated += 1
            else:
                print(f"  FAIL  {email}: {status} {resp}")
                failed += 1
            continue

        seed = (su.get("jellyfinUsername") or su.get("plexUsername")
                or su.get("username") or email.split("@")[0])
        username = make_username(seed, have_names)
        body = {
            "username": username,
            "email": email,
            "firstName": first or username,
            "lastName": last,
            "displayName": display[:100] or username,
            "isAdmin": False,
        }

        if not args.apply:
            print(f"  NEW   {email}  ->  username={username}  name={display!r}")
            created += 1
            continue

        status, resp = http("POST", f"{p_url}/api/users", {"X-API-KEY": p_key}, body)
        if status in (200, 201):
            print(f"  ADDED {email}  ({username})")
            have_emails[email.lower()] = body
            created += 1
        else:
            print(f"  FAIL  {email}: {status} {resp}")
            failed += 1

    c_verb = "created" if args.apply else "would create"
    u_verb = "renamed" if args.apply else "would rename"
    print(f"\n{created} {c_verb}, {exists} already in Pocket ID "
          f"({updated} {u_verb}), {no_email} skipped (no email), {failed} failed")
    if not args.apply:
        print("Dry run only. Re-run with --apply to create them.")


if __name__ == "__main__":
    main()
