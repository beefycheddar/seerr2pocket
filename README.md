# seerr2pocket

Copy your users from [Seerr](https://github.com/seerr-team/seerr) into [Pocket ID](https://github.com/pocket-id/pocket-id), so everyone who already requests media on your server has a Pocket ID account ready for passkey sign-in to Jellyfin, Silo, Headscale and anything else behind your OIDC provider. See [Use cases](#use-cases).

It's a single Python file with no dependencies. It runs anywhere Python 3.6+ does, including a Synology NAS over SSH.

## What it does

- Reads every user from Seerr's API.
- Creates a Pocket ID account for each one that isn't there yet, with their email, display name and a generated username.
- Leaves existing Pocket ID accounts alone, matching them by email. This makes it safe to run more than once.
- Skips Seerr users without a real email address. This is common for Jellyfin-imported users, whose username often ends up in the email field.
- Optionally (`--update-names`) copies Seerr's names onto Pocket ID accounts that already exist.
- Optionally (`--group`) adds everyone to one or more Pocket ID groups, so you can use groups to control which apps they can reach.
- **Dry run by default.** Nothing changes until you add `--apply`.

New accounts are created as **non-admins** with **no passkey**. Each user still needs a one-time setup link. You can generate one per user from the Pocket ID admin **Users** page, or, if SMTP is configured, let Pocket ID email it.

## Use cases

Seerr usually already has the most complete list of the people who use your media server: everyone who has ever requested a movie or show. seerr2pocket turns that list into Pocket ID accounts, so you don't have to recreate every user by hand when you move to single sign-on.

The script only creates accounts in Pocket ID. Apps that sign in through Pocket ID usually create their own account for a person the first time that person signs in, so once someone has a Pocket ID account and a passkey, the other apps follow on their own.

### Bring existing users into Jellyfin

With an OIDC/SSO plugin pointed at Pocket ID, Jellyfin can create a matching Jellyfin account the first time each person clicks "Sign in with Pocket ID".

1. Run seerr2pocket with a group, for example `--group jellyfin`.
2. In Pocket ID, limit the Jellyfin OIDC client to that group under **Allowed User Groups**.
3. Send each user their Pocket ID passkey setup link.
4. They sign in to Jellyfin with Pocket ID, and their Jellyfin account is created.

If your plugin can map groups to Jellyfin roles or library access, the same groups can decide what each person sees. Check your plugin's settings for whether it creates accounts automatically.

### Bring existing users into Silo

Silo works the same way through its OIDC plugin. Imported users sign in to Silo with Pocket ID, and the Silo account is created on first sign-in if the plugin is set to do that. Put Silo users in their own group (`--group silo`) if not everyone who uses Jellyfin should get Silo, or the other way around.

### One account for the whole stack

The same Pocket ID account can sign people in to anything else on your server that uses OIDC, such as Headscale/Tailscale for remote access, Seerr itself once its OIDC support is stable, and dashboards. Run the import once, give each app its own group, and control access by adding people to groups instead of managing separate logins in each app:

```bash
python3 seerr2pocket.py --group jellyfin --group silo --apply
```

### Keep Pocket ID in step with Seerr

The script is safe to run again. Re-run it after new people join Seerr to give them Pocket ID accounts and put them in the same groups. Nobody who already exists is changed unless you use `--update-names`.

## Requirements

- Python 3.6 or newer
- A Seerr API key: **Seerr → Settings → General → API Key**
- A Pocket ID API key: **Pocket ID → Settings → Admin → API Keys** (`https://<your-pocket-id>/settings/admin/api-keys`)
- Network access from wherever you run the script to both Seerr and Pocket ID

## Usage

```bash
curl -O https://raw.githubusercontent.com/beefycheddar/seerr2pocket/main/seerr2pocket.py

export SEERR_URL=http://seerr.local:5055
export SEERR_API_KEY=your-seerr-api-key
export POCKET_URL=https://id.example.com
export POCKET_API_KEY=your-pocket-id-api-key

python3 seerr2pocket.py            # dry run: shows what would be created
python3 seerr2pocket.py --apply    # creates the users
```

Each `export` lasts only for the current shell session, so your API keys are never written to disk.

### Options

| Flag | What it does |
|---|---|
| `--apply` | Make the changes. Without this, nothing is written. |
| `--update-names` | For users already in Pocket ID (matched by email), replace first name, last name and display name with Seerr's. Username, email, admin status and enabled/disabled are kept as they are. |
| `--group NAME` | Add every Seerr user that has a Pocket ID account (new and existing) to this group. Matches the group's name or friendly name, ignoring case. Repeat it for several groups: `--group family --group media`. |

### Groups

The group has to exist already; create it in the Pocket ID admin UI first. If the name doesn't match any group, the script stops before changing anything and lists the groups it found.

Members are only ever **added**. Anyone already in the group stays there, including people who aren't in Seerr. Running it again only adds whoever is missing.

A common setup is a `family` group, with each OIDC client (Jellyfin, Seerr, and so on) limited to that group under **Allowed User Groups** in Pocket ID:

```bash
python3 seerr2pocket.py --group family            # preview
python3 seerr2pocket.py --group family --apply    # create users and add them to the group
```

### Example output

```
Seerr users: 5   Pocket ID users: 2

  HAVE  admin@example.com
  NEW   alice@example.com  ->  username=alice  name='Alice Smith'
  NEW   bob@example.com  ->  username=bobby  name='bobby'
  SKIP  'carol': no real email (got 'carol')
  HAVE  dave@example.com

2 would create, 2 already in Pocket ID (0 would rename), 1 skipped (no email), 0 failed
Dry run only. Re-run with --apply to create them.
```

| Status | Meaning |
|---|---|
| `NEW` / `ADDED` | Would be created / was created |
| `HAVE` | Already in Pocket ID (same email); left alone |
| `NAME` | Name would change / changed (`--update-names`), shown old → new |
| `SAME` | Already in Pocket ID with identical names |
| `SKIP` | Seerr has no usable email for this user |
| `JOIN` | Would be added / was added to a group (`--group`) |
| `GROUP` | Group note, such as everyone already being a member |
| `FAIL` | Pocket ID rejected the request; the error follows |

## How fields are mapped

| Pocket ID | Taken from Seerr |
|---|---|
| Email | `email` |
| Display name | `displayName`, falling back to username, Jellyfin username, Plex username, then the part of the email before `@` |
| First name / last name | Display name split at the first space: `Alice Smith` → `Alice` / `Smith` |
| Username | Jellyfin username, Plex username, Seerr username or email prefix, lowercased and stripped to `a–z 0–9 _ . -`. A number is added if the name is already taken |

## Things to know

- **Matching is by email only.** If someone uses a different email in Seerr than in Pocket ID, the script treats them as two people and creates a second account. The reverse also holds: Pocket ID accounts whose email isn't in Seerr are never touched.
- **Seerr names are often usernames.** Many Seerr display names are just a Jellyfin or Plex handle, so `--update-names` can replace a real name like "Alice Smith" with "asmith42". Review the dry run before you apply it.
- **One person with two Seerr accounts gets two Pocket ID accounts.** If the same person signed up in Seerr twice with different emails, check for duplicates afterward.
- **LDAP-synced Pocket ID users and groups are never edited.**
- **`--group` includes existing accounts, yours too.** Every Seerr user with a Pocket ID account is added, not only the ones created in this run. Check the `JOIN` lines in the dry run.
- **Admin rights are never granted.** Promote people in the Pocket ID UI if needed.

## Running on a Synology NAS

DSM ships with `python3`. Copy the script to a shared folder (for example `/volume1/scripts`), SSH in, and run the commands above. If Seerr runs in Container Manager on the same box, `SEERR_URL` can point at the NAS IP and the Seerr port.

## License

MIT. See [LICENSE](LICENSE).

Not affiliated with the Seerr or Pocket ID projects.
