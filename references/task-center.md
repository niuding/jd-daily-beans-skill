# JD PC task centre: field notes

Verified against the JD PC site in November 2026. Selectors carry hashed CSS
suffixes (e.g. `_checkInBtn_eu7cg_1043`), so always match on the stable prefix
with `[class*="..."]` rather than the full class name.

## Where the tasks actually are

The PC "今日任务" panel is **not** in 我的京东. It is rendered by the
`jdmcc-signin` widget at the top-right of a `search.jd.com` results page:

```
li.shortcut_btn.fore2.jdmcc-signin
└── div.jdmcc-signin_pill[role=button]     ← click to expand .task-list
    └── div.jdmcc-signin__panel / .task-list
```

Pill states observed:

| class | meaning |
|---|---|
| `state_earn_more` | signed in, tasks available; pill text 「赚更多」 |
| `state_all_done` | sign-in and all tasks finished; pill text 「签到中心」 |
| (pre-sign-in) | pill invites sign-in |

Expand with `click --tab <id> '[class*="jdmcc-signin_pill"]'`. Read state from
`.task-list` and `.task-row__btn` (text is either `去完成` or `已完成`).

Typical daily menu (6 groups, 11 actions, +2 京豆 each):
浏览11.11会场 (1), 浏览互动抽奖 (1), 浏览场景频道 (2), 浏览娱乐中心 (1),
浏览频道会场 (3), 浏览点击商品 (3).

## Endpoints seen while a task runs

`api.m.jd.com` client calls:
`pc_interact_queryInteractiveInfo`, `pc_interact_queryMyAccountInfo`,
`pc_interact_doInteractiveAssignment` (`actionType=1` start, `2` finish).
`agent-browser-cli network start/list --tab <id>` is enough to inspect them.

## Why a plain loop fails in a background tab

Three independent blockers, all caused by the tab being hidden:

1. `document.visibilityState === "hidden"` — the SDK's 5-second browse timer
   never starts. Override `visibilityState`, `hidden`, `hasFocus` with
   `Object.defineProperty`, and replace `requestAnimationFrame` with
   `setTimeout` (rAF is throttled in hidden tabs).
2. Clicking `去完成` calls `window.open(...)` on a `pro.jd.com/mall/active/...`
   venue. Chrome blocks popups from background tabs, so it returns `null` and
   the SDK aborts without a window handle. Replace `window.open` with a stub
   object exposing `closed:false`, `focus`, `blur`, `close`, `postMessage`,
   `location.href`, `document.visibilityState = "visible"`. This avoids opening
   a real tab and avoids stealing focus.
3. The SDK restarts its timer on visibility events. After **every** trusted
   click, dispatch `document.dispatchEvent(new Event("visibilitychange"))` and
   `window.dispatchEvent(new Event("focus"))`, then wait 6–7 seconds.

Also: `element.click()` from `exec` is an untrusted event and does not trigger
the SDK. Use `agent-browser-cli click --tab <id> "<css-selector>"` (CDP input).
`click` accepts CSS selectors directly, which avoids a full `snapshot` each
round and avoids `@e` refs that break when the daemon restarts.

## agent-browser-cli gotchas

- The daemon starts on demand and exits when idle. Call `restart` before a
  batch and retry once after a failure. `doctor` reporting the API port down
  while the daemon is idle is expected, not a failure.
- Newly opened background tabs must be synced into the daemon session table;
  otherwise `exec` returns 「会话ID未连接」. Call `tabs` once and retry `exec`.
- `bean.jd.com/` (root) returns HTTP 403; use
  `bean.jd.com/myJingBean/list` for balance and 收支明细.
- `www.jd.com` may redirect to the global site, `m.jd.com` shows a separate
  (usually logged-out) mobile session, `vip.jd.com` is a QR-code landing page.
- On Windows, Git Bash `/tmp` is not visible to Windows Python. Pass CLI JSON
  through stdin pipes instead of temp files.
- **Driving the CLI from Python on Windows**: the npm shim is a `.cmd` file, so
  the call goes through `cmd.exe`. Two consequences:
  - `exec` code is evaluated as an async-function body and **must start with
    `return`**. A bare IIFE without `return` produces no ACK and hangs until
    the CLI's 15s timeout.
  - Embedded **newlines in the JS argument get truncated** by `cmd.exe`, giving
    `SyntaxError: Unexpected end of input at new AsyncFunction`. Flatten every
    snippet to a single line before passing it.
  - The `restart` command spawns the long-lived daemon as a child that inherits
    stdout. Capture output into a **temporary file**, not a pipe — with a pipe,
    `communicate()` waits forever for EOF even after the CLI has exited.
- Newly opened tabs can return 「会话ID xxx 未连接」 for several seconds. Poll
  `tabs` and retry `exec`; restarting the daemon once mid-wait re-registers
  sessions.

## Dead ends (do not repeat)

- 东东农场 / 宠汪汪 / 种豆得豆 / 京豆游戏 are JD **APP-native** venues reached
  by in-app search codes (e.g. 「农场活动666」). PC search returns products only.
- `bean.m.jd.com/plantBean/index.action` and `bean.m.jd.com/` → JFE 52 error.
- `jdmall.m.jd.com/beansForPrizes` → 网络异常.
- `joycenter.jd.com/play` → entertainment centre (novels/mini games), no beans.
- `home.jd.com` → 我的活动 contains only 预约 / 拍卖 / 回收单.
- `bean.jd.com/myJingBean/list` → 赚更多京豆 lists only 购物返豆 / 评价 / 晒单;
  the latter two would publish content under the user's account and are
  deliberately excluded from automation.

## Safety boundary

Automation covers only the daily sign-in and "browse for 5 seconds" tasks.
Everything that changes account state (purchase, cart, follow, review, 晒单,
share, publishing content) is out of scope and must be confirmed by the user.
All page-level patches are in-memory and disappear when the tab closes.
