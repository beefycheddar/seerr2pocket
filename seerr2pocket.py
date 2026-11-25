#!/usr/bin/env python3
"""
seerr2pocket.py - copy users (name + email) from Seerr into Pocket ID.

One-time migration. Python 3.6+, standard library only (no pip installs).
https://github.com/<you>/seerr2pocket

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
  python3 seerr2pocket.py --group family --apply   # create + add everyone to "family"
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


def pocket_groups(base, key):
    groups, page = [], 1
    while True:
        status, data = http(
            "GET",
            f"{base}/api/user-groups?pagination%5Bpage%5D={page}&pagination%5Blimit%5D=100",
            {"X-API-KEY": key},
        )
        if status != 200:
            sys.exit(f"Pocket ID group list failed ({status}): {data}")
        rows = data.get("data", []) if isinstance(data, dict) else data
        groups.extend(rows)
        pag = data.get("pagination", {}) if isinstance(data, dict) else {}
        if not rows or page >= pag.get("totalPages", 1):
            return groups
        page += 1


def resolve_groups(wanted, groups):
    """Match each requested name against a group's name or friendly name."""
    out = []
    for w in wanted:
        hit = [g for g in groups
               if w.lower() in ((g.get("name") or "").lower(),
                                (g.get("friendlyName") or "").lower())]
        if not hit:
            avail = ", ".join(sorted(g.get("name", "?") for g in groups)) or "(none)"
            sys.exit(f"Pocket ID group {w!r} not found. Create it in the Pocket ID "
                     f"admin UI first. Existing groups: {avail}")
        out.append(hit[0])
    return out


def assign_groups(base, key, groups, targets, apply):
    """Add target users to each group, keeping everyone already in it.
    targets: list of (email, user_id); user_id is None for users not created yet (dry run).
    Returns (joins, failures)."""
    joins = fails = 0
    for g in groups:
        label = g.get("friendlyName") or g.get("name")
        if g.get("ldapId"):
            print(f"  GROUP {label}: LDAP-managed, not editing")
            continue
        status, detail = http("GET", f"{base}/api/user-groups/{g['id']}",
                              {"X-API-KEY": key})
        if status != 200:
            print(f"  FAIL  group {label}: {status} {detail}")
            fails += 1
            continue
        members = {u["id"] for u in (detail.get("users") or [])}
        adding = [(e, uid) for e, uid in targets if uid is None or uid not in members]
        if not adding:
            print(f"  GROUP {label}: everyone already in it")
            continue
        for e, _ in adding:
            print(f"  JOIN  {e}  ->  {label}")
        if apply:
            ids = sorted(members | {uid for _, uid in adding if uid})
            status, resp = http("PUT", f"{base}/api/user-groups/{g['id']}/users",
                                {"X-API-KEY": key}, {"userIds": ids})
            if status not in (200, 201):
                print(f"  FAIL  group {label}: {status} {resp}")
                fails += 1
                continue
        joins += len(adding)
    return joins, fails


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
    ap.add_argument("--group", action="append", default=[], metavar="NAME",
                    help="add every Seerr user found in Pocket ID (new and existing) "
                         "to this Pocket ID group; repeat for more than one group")
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
    groups = resolve_groups(args.group, pocket_groups(p_url, p_key)) if args.group else []
    print(f"Seerr users: {len(src)}   Pocket ID users: {len(have_emails)}")
    if groups:
        print("Groups: " + ", ".join(g.get("friendlyName") or g.get("name") for g in groups))
    print()

    targets = []  # (email, pocket user id) for group assignment
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
            targets.append((email, existing.get("id")))
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
            targets.append((email, None))
            created += 1
            continue

        status, resp = http("POST", f"{p_url}/api/users", {"X-API-KEY": p_key}, body)
        if status in (200, 201):
            print(f"  ADDED {email}  ({username})")
            have_emails[email.lower()] = body
            targets.append((email, resp.get("id") if isinstance(resp, dict) else None))
            created += 1
        else:
            print(f"  FAIL  {email}: {status} {resp}")
            failed += 1

    joins = 0
    if groups:
        if args.apply:
            # Look up IDs fresh so newly created users are included even if the
            # create response didn't return one.
            fresh, _ = pocket_existing(p_url, p_key)
            targets = [(e, (fresh.get(e.lower()) or {}).get("id")) for e, _ in targets]
            targets = [(e, uid) for e, uid in targets if uid]
        print()
        joins, gfail = assign_groups(p_url, p_key, groups, targets, args.apply)
        failed += gfail

    c_verb = "created" if args.apply else "would create"
    u_verb = "renamed" if args.apply else "would rename"
    j_verb = "group additions" if args.apply else "group additions pending"
    summary = (f"\n{created} {c_verb}, {exists} already in Pocket ID "
               f"({updated} {u_verb}), {no_email} skipped (no email)")
    if groups:
        summary += f", {joins} {j_verb}"
    print(summary + f", {failed} failed")
    if not args.apply:
        print("Dry run only. Re-run with --apply to make these changes.")


if __name__ == "__main__":
    main()
