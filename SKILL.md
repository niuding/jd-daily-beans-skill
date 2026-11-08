---
name: jd-daily-beans
description: Automates the daily JD (京东 / Jingdong) bean sign-in and the hidden PC "今日任务" browse tasks through agent-browser-cli in background Chrome tabs. This skill should be used when the user asks to collect/sign for 京豆, run JD daily tasks, check JD bean balance, or set up a recurring JD bean job. It covers the non-obvious facts that the PC task centre lives in the search-page sign-in capsule, that background tabs need visibility/page-handle patches for the 5-second timer, and that the daily guard must only be written on success. It never performs purchases, follows, cart additions, reviews, or sharing.
agent_created: true
---

# JD Daily Beans

## Overview

Collect the daily JD (京东) bean rewards without touching the user's foreground
browser. Two independent sources are handled:

1. **网页端天天签到领京豆** on `home.jd.com` (the "立即签到" button).
2. **PC 端「今日任务」浏览任务** on the search page — 6 groups / 11 browse
   actions, +2 京豆 each (22 京豆/day at the time of writing).

Farm / pet / "东东农场" / "宠汪汪" / "种豆得豆" style games are JD **APP-native**
venues and have no PC Web entry point. Do not waste time probing old
`bean.m.jd.com/plantBean` URLs — see `references/task-center.md`.

## Prerequisites

- `agent-browser-cli` installed and its Chrome extension loaded, Chrome running.
- The user is already logged into JD in that Chrome profile. Never handle,
  print, or store cookies / credentials.
- Python 3.9+.

## Quick start

Run the bundled script instead of re-deriving the CLI sequence:

```bash
python scripts/jd_daily_beans.py --dry-run     # inspect only, no clicks
python scripts/jd_daily_beans.py               # run once; skips if already done today
python scripts/jd_daily_beans.py --force       # bypass the once-per-day guard
```

Configuration lives in `config.json` (copy `config.example.json`; the real file
is gitignored). `cli_path` may be left empty for auto-detection, or set via the
`AGENT_BROWSER_CLI` environment variable. `--config PATH` overrides the path.

Exit codes: `0` = success or already-done, `1` = failure (state deliberately
**not** marked done, so a later scheduled trigger retries).

## Workflow

1. **Verify the bridge.** `agent-browser-cli restart`, then `status`. Abort if
   `connection.extension_connected` is false — Chrome or the extension is not
   running; do not mark the day as done.
2. **Sign in.** `open --background https://home.jd.com/`, wait for the tab
   session to sync, probe for a button containing `立即签到`. If present, click
   `[class*="_checkInBtn_"]` with the CLI's trusted click. If it says
   `去使用`, sign-in is already done.
3. **Open the hidden task centre.** `open --background` on the search URL, then
   click `[class*="jdmcc-signin_pill"]` to expand `.task-list`. A pill with
   `state_all_done` means every task is already finished.
4. **Patch the background tab** with `INJECT_VISIBILITY` in
   `scripts/jd_daily_beans.py`: override `visibilityState`/`hidden`/`hasFocus`,
   replace `requestAnimationFrame`, and stub `window.open` with a fake window
   handle. Patch in page memory only; it disappears when the tab closes.
5. **Loop the browse tasks.** For each round: mark the first button whose text
   is `去完成` with `id="jd-auto-target"`, run
   `click --tab <id> "#jd-auto-target"` (a CDP trusted click is mandatory —
   `element.click()` is untrusted and sends no request), wait ~1s, dispatch
   `visibilitychange` + `focus`, wait 6–7s, re-read task state. Stop when
   `.task-list` has no `去完成` buttons.
6. **Verify.** Open `bean.jd.com/myJingBean/list` in the background and read the
   balance/明细. Note `bean.jd.com/` (root) returns 403.
7. **Clean up.** Close only the tabs opened by this run; leave user tabs alone.
8. **Mark the day done** only after all steps succeeded. The guard lives in
   `<data_dir>/last_run.json`.

Detailed mechanics, API names, and failure modes: `references/task-center.md`.

## Hard rules

- Always `open --background` and always pin commands with `--tab <id>`.
  Never focus or activate a window.
- Never perform actions that change the account's social/commercial state:
  no purchases, cart additions, shop follows, reviews, 晒单, sharing, or
  publishing content. If a task requires one, report it and ask first.
- Never log cookies, account names, tokens, or full page text containing PII.
- Use CSS selectors, not `snapshot` `@e` refs: refs are invalidated whenever
  the CLI daemon restarts, and the daemon exits when idle.
- Keep repo/public artefacts free of machine-specific absolute paths and any
  real account data.

## OS-level scheduling

`scripts/install_task.ps1` registers a Windows Task Scheduler job that fires at
every whole hour; the script's once-per-day guard makes all later triggers
no-ops. `scripts/uninstall_task.ps1` removes it. See `README.md` for cron on
macOS/Linux.
