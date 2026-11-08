# jd-daily-beans skill

A [WorkBuddy](https://www.workbuddy.cn/) skill + standalone script that collects
JD (京东) daily 京豆 rewards **in the background**, using
[`agent-browser-cli`](https://github.com/sleepinginsummer/agent-browser-cli).

It handles:

- 网页端天天签到领京豆 on `home.jd.com`
- the hidden PC「今日任务」centre: 6 groups / 11 browse tasks, +2 京豆 each

No purchases, shop follows, cart additions, reviews, 晒单, sharing, or any
other action that changes the account's social/commercial state. No credentials,
cookies, or tokens are read, logged, or stored. The foreground window is never
stolen: every page is opened with `open --background` and every command is
pinned with `--tab <id>`.

> Personal automation only. Respect JD's terms of service and use at your own
> discretion. Rewards, task lists, and selectors may change without notice.

## Requirements

| | |
|---|---|
| OS | Windows 10/11 (macOS/Linux work with cron; see below) |
| Python | 3.9+ |
| Browser | Google Chrome with the `agent-browser-cli` extension loaded |
| CLI | `npm i -g @sleepinsummer/agent-browser-cli` |
| JD session | already logged in inside that Chrome profile |

The script **never logs in for you**; it detects a logged-out session and exits
with code 1 so a later scheduled run can retry.

## Install as a WorkBuddy skill

Copy this directory to your user skills folder:

```text
~/.workbuddy/skills/jd-daily-beans/          # all projects
<project>/.workbuddy/skills/jd-daily-beans/  # one project only
```

Then create your local config (gitignored, may contain absolute paths):

```bash
cp config.example.json config.json
```

`cli_path` can stay empty — the script auto-detects the CLI. Override with the
`AGENT_BROWSER_CLI` environment variable or `config.json` if needed.

## Run manually

```bash
python scripts/jd_daily_beans.py --dry-run   # inspect only; no clicks
python scripts/jd_daily_beans.py             # run once
python scripts/jd_daily_beans.py --force     # ignore the once-per-day guard
```

On Windows, `scripts\run_daily.cmd` is a convenience wrapper that resolves the
Python interpreter and passes `config.json`.

A run opens three temporary background tabs (home / search / bean list), closes
them afterwards, and writes:

- `<data_dir>/logs/YYYY-MM-DD.log` — full run log
- `<data_dir>/last_run.json` — once-per-day guard, written **only on success**

`<data_dir>` defaults to `%LOCALAPPDATA%\jd-daily-beans` on Windows and
`~/.jd-daily-beans` elsewhere; override with `log_dir` / `state_dir`.

Exit codes: `0` success or already-done, `1` failure (the day is not marked
done, so the next hourly trigger retries).

## OS-level scheduling (Windows)

`install_task.ps1` creates a Task Scheduler job that fires **on every whole
hour**; the script's once-per-day guard makes all later triggers no-ops. Missed
runs (machine asleep / not logged on) are started as soon as possible afterwards.
It also sets `earliest_hour: 8` in the generated config, so midnight-to-08:00
triggers are skipped *without* writing the daily guard — this avoids reading
yesterday's "all done" task panel before JD refreshes it. Set `earliest_hour: 0`
in `config.json` to disable that window.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_task.ps1
# optional: -TaskName MyJob -PythonExe C:\path\to\pythonw.exe
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall_task.ps1
```

The installer auto-detects `pythonw.exe` (no console window), writes a local
`config.json` and `scripts/.runtime.cmd`, and registers the task for the
**current interactive user** (required: Chrome and its extension live in that
session). It does not require administrator rights.

Verify / inspect:

```powershell
Get-ScheduledTask -TaskName JD-Daily-Beans | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName JD-Daily-Beans |
    Select-Object LastRunTime, LastTaskResult, NextRunTime
```

To trigger only once a day at a specific hour instead, edit the trigger line in
`install_task.ps1` to `New-ScheduledTaskTrigger -Daily -At 09:00` and remove the
repetition arguments.

## OS-level scheduling (macOS / Linux)

```cron
0 * * * * /usr/bin/python3 /path/to/jd-daily-beans/scripts/jd_daily_beans.py --config /path/to/jd-daily-beans/config.json
```

The once-per-day guard applies here too. `cron` jobs inherit a minimal
environment — set `AGENT_BROWSER_CLI` in the crontab or in `config.json`.

## Layout

```text
SKILL.md                     skill instructions (WorkBuddy metadata)
README.md                    this file
config.example.json          public config template
scripts/
  jd_daily_beans.py          main automation
  run_daily.cmd              manual Windows runner
  install_task.ps1           register OS scheduled task
  uninstall_task.ps1         remove OS scheduled task
references/
  task-center.md             selectors, SDK mechanics, dead ends
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `extension_connected: false` | Chrome not running or extension not loaded; the run aborts and retries next hour |
| `JD login not detected` | Log into JD once in that Chrome profile |
| 「会话ID未连接」 | New background tab not yet synced — the script calls `tabs` and retries |
| Tasks never advance | The visibility patch / `visibilitychange` dispatch did not apply; see `references/task-center.md` |
| `state_all_done` on first run | All tasks already finished today; this is normal |
| CLI not found | Set `cli_path` in `config.json` or `AGENT_BROWSER_CLI` |

## License

MIT — see `LICENSE`.
