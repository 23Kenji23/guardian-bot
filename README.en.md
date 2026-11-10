# 🛡️ Guardian

**A simple monitoring bot that runs on your own phone, PC or server.** It watches your websites and ports and tells you — on Telegram, Discord, Slack, ntfy or e-mail — when something goes down and when it comes back.

[Versão em português → README.md](README.md)

- **One file** (`guardian.py`), **nothing to install**: Python 3.11+ standard library only.
- **Runs anywhere Python 3.11+ does**: Linux, macOS, Windows, Raspberry Pi, Termux on Android. (Automated tests run on Linux; elsewhere it is the same stdlib-only code, but not tested there.)
- **Your data stays yours**: no account, no cloud, no Guardian server. It only talks to what *you* configure.
- **No false alarms**: it says "down" only after several failures in a row (you choose how many), and says it once, not every minute.
- **MIT** licensed: use, copy, modify, sell, anything.

## What it does / does not do

| Does | Does not |
|---|---|
| Checks websites (HTTP/HTTPS) and TCP ports | Charts or long history (keeps the last 200 events) |
| Checks status code and expected text on the page | Monitor from inside the server (CPU, disk…) |
| Warns when an SSL certificate is about to expire | Alert *by itself* if its own device dies (that takes an outside service: see "Who watches the watcher") |
| **Watches your scripts and `cron` jobs**: alerts when their "sign of life" stops | Web dashboard with logins |
| **Telegram control**: `/pause`, `/resume`, `/check` (maintenance without alarms) | |
| **External heartbeat**: an outside service alerts if Guardian or its device dies | |
| Optional read-only local API and status page | |

## Quick start

```sh
git clone https://github.com/23Kenji23/guardian-bot.git
cd guardian-bot
python3 guardian.py init          # writes an example guardian.toml
# edit guardian.toml: replace the example site with yours
python3 guardian.py check         # check once now (exit code 1 if anything is down)
python3 guardian.py run           # watch continuously
```

Minimum config:

```toml
[guardian]
language = "en"

[[check]]
name = "My site"
url = "https://mysite.com"
```

With no alert channel enabled, alerts are printed to the screen.

## Commands

| Command | What it does |
|---|---|
| `guardian.py init` | Creates an example `guardian.toml` (never overwrites) |
| `guardian.py check` | Checks everything once. Exit `0` = all up, `1` = something down (great for `cron`) |
| `guardian.py run` | Watches continuously and alerts |
| `guardian.py test-notify` | Sends a test message to the enabled channels |
| `-c other.toml` | Use another config file (or set `GUARDIAN_CONFIG`) |

## Config reference

`[guardian]`: `language` (`"pt"`/`"en"`), `interval` (default 60 s, min 5), `timeout` (10 s), `failures_to_alert` (2), `remind_minutes` (0 = don't repeat), `state_file`.

`[[check]]` HTTP: `name`, `url`, optional `expect_status` (default any 200–399), `contains` (text that must appear), `interval`, `timeout`, `cert_warn_days` (14; 0 = off).
`[[check]]` TCP: `name`, `type = "tcp"`, `host`, `port`.
`[[check]]` push (your script gives the signal): `name`, `type = "push"`, `interval` (required: how often the signal should arrive, seconds), `grace` (seconds of slack, default 60).

Secrets go in **environment variables** (`token_env`, `url_env`, `password_env`), not in the file.

### Telegram

1. Talk to **@BotFather**, send `/newbot`, copy the **token**.
2. Send any message to your new bot.
3. Run `curl "https://api.telegram.org/bot<TOKEN>/getUpdates"` and find `"chat":{"id":123456789` — that is your `chat_id`.
4. ```toml
   [notify.telegram]
   enabled = true
   token_env = "GUARDIAN_TELEGRAM_TOKEN"
   chat_id = "123456789"
   ```
5. `export GUARDIAN_TELEGRAM_TOKEN="123456:ABC..."` then `python3 guardian.py test-notify`.

The bot only obeys your `chat_id` (careful: if it is a **group**, everyone in the group controls the bot).

| Command | What it does |
|---|---|
| `/status` | State of everything (and active pauses) |
| `/check` or `/check now` | Checks everything now. `/check My site` checks one item |
| `/pause` | Silences **all** alerts for 1 hour |
| `/pause 30m` · `/pause 2h` · `/pause 1d` · `/pause 1h30m` | Silences for that long (1 minute to 7 days). A bare number = minutes |
| `/pause 2h My site` | Silences only that item (name may contain spaces, case-insensitive) |
| `/resume` · `/resume My site` | Ends the pause early |
| `/help` | Lists commands (`/maintenance` is another name for `/pause`; Portuguese names work too) |

**How a pause works (so you can take a site down on purpose without being nagged):**

- Guardian **keeps checking** during a pause: `/status` and the status page show the truth. Only **alerts** go quiet.
- When the pause ends (or on `/resume`), anything **still down** is announced right away ("X is still down after …"). If it came back by itself, there is nothing left to say.
- If you had **already been told** something was down and it recovers during the pause, the "back up" message is delivered anyway (otherwise the outage would never be closed).
- The pause is saved to disk: restarting Guardian does not cancel it. If it ends while Guardian is off, the end-of-pause message goes out when it starts.
- These commands only touch **alerts**. They execute nothing and never change `guardian.toml`.

### Webhook / e-mail

```toml
[notify.webhook]
enabled = true
url_env = "GUARDIAN_WEBHOOK_URL"
format = "discord"          # "discord" | "slack" | "ntfy" | "generic" (JSON)

[notify.email]
enabled = true
host = "smtp.example.com"
port = 587
security = "starttls"       # "starttls" | "ssl" | "none" (local server only)
username = "you@example.com"
password_env = "GUARDIAN_SMTP_PASSWORD"
from = "you@example.com"
to = ["you@example.com"]
```

Failed deliveries are retried 3 times and logged; one broken channel never blocks the others or the checks.

## Who watches the watcher

Guardian measures from where it runs. If the phone shuts off, the battery dies or the internet drops, **it cannot tell you it stopped**. The alert has to come from *outside*. So Guardian does the opposite: it **sends a sign of life** to an external service, and the service alerts you when the signals stop.

```toml
[heartbeat]
enabled = true
url_env = "GUARDIAN_HEARTBEAT_URL"   # the service's ping URL (a secret: keep it in the environment)
interval = 60                        # seconds between signals (10 to 3600)
```

1. Create a "push" / heartbeat monitor on an external service (for example **healthchecks.io**, which has a free plan, or **Uptime Kuma**'s push monitor hosted elsewhere) and point its alert at your e-mail/Telegram. Set its tolerance to about 2–3 intervals.
2. Put the ping URL it gives you in the `GUARDIAN_HEARTBEAT_URL` environment variable.
3. Try `python3 guardian.py test-notify` (it sends one signal and prints `OK heartbeat`).

Guardian does a plain `GET` on that URL, and **only while its main loop is actually running**: if it hangs (not just dies), the signal stops too. Off, offline, hung or killed → the external service stops hearing from it and alerts you.

> I tested the signal against a fake server that records the `GET`s, not against the real healthchecks.io or Uptime Kuma. A plain `GET` on the URL is the format they use, but verify it with their own test button.

## Watching scripts and `cron` jobs

A backup that stopped running does not "go down": it just **stops giving a sign of life**. That is what the `push` check is for. Your script says "I'm alive", and Guardian alerts when the signal stops arriving.

```toml
[api]
enabled = true
token_env = "GUARDIAN_API_TOKEN"     # mandatory for push checks

[[check]]
name = "Backup"
type = "push"
interval = 86400                     # a signal is due every 24 h...
grace = 3600                         # ...with 1 h of slack
```

At the end of your script (only if it succeeded) or in `cron`:

```sh
curl -fsS -m 10 -X POST -H "Authorization: Bearer $GUARDIAN_API_TOKEN" http://127.0.0.1:8765/ping/Backup
# if the script knows it failed, say so right away instead of waiting for the deadline:
curl -fsS -m 10 -H "Authorization: Bearer $GUARDIAN_API_TOKEN" http://127.0.0.1:8765/ping/Backup/fail
```

- A signal later than `interval + grace` → "no signal for …" alert. A missed signal is proof enough: **it does not wait for a second failure**, and neither does `/fail`.
- The next signal → "back up". Names with spaces become `%20` in the URL; prefer names without spaces.
- Freshly started, Guardian gives a full window before expecting a signal, so restarting causes no false alarm.
- This is the **only exception** to the API being read-only: it exists only with a token, accepts only `GET`/`POST` on `/ping/<item>` (never `HEAD`), only works for `push` items and only updates that item's clock. See [SECURITY.md](SECURITY.md).
- `guardian.py check` (the one-off check) cannot verify `push` items: it lists them as `SKIP`.

## Local API and status page (optional)

```toml
[api]
enabled = true
host = "127.0.0.1"
port = 8765
```

`/` status page · `/api/v1/status` · `/api/v1/checks` · `/api/v1/events` · `/health` · `/ping/<item>` (push items only, **always requires a token**). Read-only (other methods → 405) except `/ping/`. Users, passwords and query strings in your URLs are never shown. To expose it beyond localhost you **must** set `token_env`; use `Authorization: Bearer <token>` (or any username + the token as password in a browser).

## Keep it running

- **Linux:** a `systemd --user` service (see the Portuguese README for a ready unit file).
- **Android (Termux):** `pkg install python`, use `tmux` or a Termux:Boot script with `termux-wake-lock`. *Not yet tested on a real Android device.*
- **Windows:** Task Scheduler, "at startup".
- **Without a daemon:** `guardian.py check` in `cron`.

## Security in short

No received text ever becomes a command: no shell, no subprocess. Inputs are only your `guardian.toml` and the commands (`/status`, `/check`, `/pause`, `/resume`, `/help`) from *your* chat, which only affect alerts. Secrets (including the heartbeat URL) stay in the environment and are masked (`***`) in logs; the state file is written `0600`; redirects only follow `http(s)`. See [SECURITY.md](SECURITY.md).

## Limits

- **If the device running Guardian is off or offline, Guardian itself cannot alert you.** The external service behind `[heartbeat]` does (see "Who watches the watcher"). **Without `[heartbeat]`, no alert arrives.** It also measures from where it runs, so a local network outage looks like your site being down.
- No charts, no long history, no multi-user. On purpose: small enough to read in half an hour.

## Tests

`python3 -m unittest discover -s tests -v` — 76 tests, all local (fake HTTP, Telegram, webhook, SMTP and heartbeat servers). Run on Python 3.11–3.13 via GitHub Actions.

## About this project

Guardian is a small, complete bot released as-is. **No guaranteed support and no roadmap.** It is MIT so you can fork and adapt it freely. Bug and security fixes are welcome via issues or pull requests.

## License

[MIT](LICENSE).
