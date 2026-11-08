#!/usr/bin/env python3
"""JD daily beans: sign in + PC task-centre browse tasks, fully in background.

The automation drives an already-logged-in Chrome through agent-browser-cli.
It only performs reversible, zero-side-effect actions: the daily sign-in and
"browse for 5 seconds" tasks. It never buys, follows shops, adds to cart,
writes reviews, shares, or submits any user-generated content.

All pages are opened with `open --background` and every command is pinned to a
tab id, so the user's foreground window is never stolen.

Usage:
    python jd_daily_beans.py [--config PATH] [--dry-run] [--force]
                             [--max-rounds N] [--no-cleanup]
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "cli_path": "",
    "home_url": "https://home.jd.com/",
    "search_url": "https://search.jd.com/Search?keyword=%E4%BA%AC%E8%B1%86",
    "bean_list_url": "https://bean.jd.com/myJingBean/list",
    "task_wait_seconds": 7,
    "max_task_rounds": 16,
    "page_wait_seconds": 3,
    "allow_signin": True,
    "allow_browse_tasks": True,
    "close_temp_tabs": True,
    "earliest_hour": 0,
    "log_dir": "",
    "state_dir": "",
}

# ---------------------------------------------------------------------------
# JavaScript snippets executed in the page through `agent-browser-cli exec`
# ---------------------------------------------------------------------------

HOME_PROBE = r"""
(function () {
  var t = document.body ? (document.body.innerText || "") : "";
  var b = [...document.querySelectorAll("button")]
    .find(e => /立即签到/.test((e.innerText || "").trim()));
  var m = t.match(/([\d,]+)\s*去使用\s*京豆/);
  return {
    url: location.href,
    title: document.title,
    logged_in: !/passport\.jd\.com/.test(location.href) && /京豆/.test(t),
    signin_text: b ? (b.innerText || "").trim() : "",
    balance: m ? m[1] : "",
    text: t.slice(0, 350)
  };
})()
"""

TASK_STATE = r"""
(function () {
  var l = document.querySelector(".task-list");
  var text = l ? (l.innerText || "") : "";
  var pending = [...document.querySelectorAll(".task-row__btn")]
    .filter(e => (e.innerText || "").trim() === "去完成").length;
  return {
    url: location.href,
    title: document.title,
    panel: !!l,
    pending: pending,
    done: (text.match(/已完成/g) || []).length,
    pill: (document.querySelector(".jdmcc-signin_pill") || {}).className || "",
    text: text.slice(0, 1500)
  };
})()
"""

# Background tabs are `hidden`, so the JD SDK never starts its 5s timer; and
# window.open() is popup-blocked, so the SDK gets no window handle. Patch both
# in page memory only. Nothing persists after the tab is closed.
INJECT_VISIBILITY = r"""
(function () {
  try { Object.defineProperty(document, "visibilityState", { get: () => "visible", configurable: true }); } catch (e) {}
  try { Object.defineProperty(document, "hidden", { get: () => false, configurable: true }); } catch (e) {}
  try { Object.defineProperty(document, "hasFocus", { value: () => true, configurable: true }); } catch (e) {}
  window.requestAnimationFrame = function (cb) { return setTimeout(function () { cb(performance.now()); }, 16); };
  window.cancelAnimationFrame = function (id) { clearTimeout(id); };
  window.open = function (u) {
    return {
      closed: false,
      focus: function () {}, blur: function () {}, close: function () {},
      postMessage: function () {}, location: { href: String(u) },
      document: { visibilityState: "visible" }
    };
  };
  document.dispatchEvent(new Event("visibilitychange"));
  window.dispatchEvent(new Event("focus"));
  return { visibility: document.visibilityState, hidden: document.hidden };
})()
"""

MARK_PENDING_BUTTON = r"""
(function () {
  var b = [...document.querySelectorAll(".task-row__btn")]
    .find(e => (e.innerText || "").trim() === "去完成");
  if (!b) return "none";
  b.id = "jd-auto-target";
  var row = b.closest(".task-row");
  return row ? (row.innerText || "").replace(/\s+/g, " ").slice(0, 60) : "marked";
})()
"""

FIRE_VISIBILITY = r"""
(function () {
  document.dispatchEvent(new Event("visibilitychange"));
  window.dispatchEvent(new Event("focus"));
  return "fired";
})()
"""

BEAN_BALANCE = r"""
(function () {
  var t = document.body ? (document.body.innerText || "") : "";
  var m = t.match(/我的京豆[\s\S]{0,60}?([\d,]+)/);
  return {
    url: location.href,
    title: document.title,
    balance: m ? m[1] : "",
    plus2_rows: (t.match(/\+2\s*京豆/g) || []).length,
    text: t.slice(0, 700)
  };
})()
"""


def log(msg: str) -> None:
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    # pythonw.exe has no console; print() is a no-op for None stdout, but guard
    # it anyway so logging can never abort a scheduled run.
    try:
        print(line, flush=True)
    except (AttributeError, OSError, ValueError):
        pass
    if LOGGER["path"]:
        try:
            with open(LOGGER["path"], "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


LOGGER = {"path": ""}


def default_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "jd-daily-beans"
    return Path.home() / ".jd-daily-beans"


def load_config(path: str | None) -> dict:
    cfg = dict(DEFAULTS)
    candidate = path or os.environ.get("JD_BEANS_CONFIG") or str(SKILL_ROOT / "config.json")
    p = Path(candidate)
    if p.is_file():
        try:
            cfg.update({k: v for k, v in json.loads(p.read_text(encoding="utf-8")).items() if v is not None})
            log(f"config loaded: {p}")
        except (OSError, ValueError) as exc:
            log(f"WARNING: cannot read config {p}: {exc}")
    cfg["cli_path"] = os.environ.get("AGENT_BROWSER_CLI") or cfg.get("cli_path") or ""
    return cfg


def find_cli(configured: str) -> str:
    """Resolve the agent-browser-cli executable without any hardcoded paths."""
    if configured:
        p = Path(configured)
        if p.is_file():
            return str(p)
        log(f"WARNING: configured cli_path not found: {configured}")

    for name in ("agent-browser-cli", "agent-browser-cli.cmd"):
        found = shutil.which(name)
        if found:
            return found

    home = Path.home()
    patterns = [
        str(home / ".workbuddy" / "binaries" / "node" / "versions" / "*" / "agent-browser-cli.cmd"),
        str(home / "AppData" / "Roaming" / "npm" / "agent-browser-cli.cmd"),
        str(home / ".npm-global" / "bin" / "agent-browser-cli"),
        "/usr/local/bin/agent-browser-cli",
        "/usr/bin/agent-browser-cli",
    ]
    for pattern in patterns:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    raise RuntimeError(
        "agent-browser-cli not found. Install it (npm i -g @sleepinsummer/agent-browser-cli) "
        "or set cli_path in config.json / AGENT_BROWSER_CLI."
    )


def parse_json(text: str):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                return None
    return None


class Cli:
    def __init__(self, path: str, timeout: int = 30):
        self.path = path
        self.timeout = timeout

    def raw(self, *args, timeout: int | None = None) -> subprocess.CompletedProcess:
        """Run the CLI with its output redirected to a temporary file.

        The npm .cmd shim spawns a long-lived daemon that inherits stdout. With
        a pipe, `communicate()` waits forever for EOF even after the CLI exits.
        A regular file avoids that; inherited file handles do not block reads.
        """
        fd, tmp = tempfile.mkstemp(prefix="jd_cli_", suffix=".out")
        os.close(fd)
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        try:
            with open(tmp, "w", encoding="utf-8") as out:
                proc = subprocess.Popen(
                    [self.path, *args],
                    stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    shell=False, creationflags=flags,
                )
                try:
                    rc = proc.wait(timeout=timeout or self.timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    raise
            text = Path(tmp).read_text(encoding="utf-8", errors="replace")
            return subprocess.CompletedProcess([self.path, *args], rc, text, "")
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass  # daemon may still hold the handle; temp file is tiny

    def json(self, *args, timeout: int | None = None) -> dict:
        last: Exception | None = None
        for attempt in range(3):
            try:
                proc = self.raw(*args, timeout=timeout)
                data = parse_json(proc.stdout)
                if not isinstance(data, dict):
                    raise RuntimeError(f"unexpected CLI output: {(proc.stdout or proc.stderr)[:200]}")
                return data
            except (subprocess.TimeoutExpired, OSError, RuntimeError) as exc:
                last = exc
                log(f"CLI retry {attempt + 1}/3 after: {type(exc).__name__}: {str(exc)[:120]}")
                try:
                    self.raw("restart", timeout=20)
                except Exception:
                    pass
                time.sleep(1)
        raise RuntimeError(f"agent-browser-cli unavailable: {last}")

    def restart(self) -> None:
        self.json("restart", timeout=25)
        time.sleep(1)

    def tabs(self) -> list:
        return self.json("tabs").get("result", {}).get("metadata", {}).get("tabs", []) or []

    def open_bg(self, url: str) -> str:
        data = self.json("open", "--background", url)
        tid = str(data.get("result", {}).get("opened_tab_id", ""))
        if not tid:
            raise RuntimeError(f"open --background failed: {str(data)[:200]}")
        # Let the extension register the new session before the first exec.
        time.sleep(1)
        try:
            self.tabs()
        except Exception:
            pass
        return tid

    def js(self, tab: str, code: str, tries: int = 3):
        # `exec` evaluates the code as a function body: a bare IIFE without an
        # explicit `return` never ACKs and hangs until the CLI times out.
        stripped = code.strip()
        if not stripped.startswith("return "):
            code = "return " + stripped
        # On Windows the npm .cmd shim goes through cmd.exe, which truncates an
        # argument at an embedded newline. Flatten to one line before invoking.
        # The snippets contain no line comments, so joining with spaces is safe.
        code = " ".join(part.strip() for part in code.splitlines() if part.strip())
        last = "unknown exec error"
        for attempt in range(tries):
            data = self.json("exec", "--tab", tab, code)
            if not data.get("ok"):
                last = f"exec failed: {str(data)[:180]}"
            else:
                value = data.get("result", {}).get("js_return")
                # The bridge reports delivery problems as a plain string.
                if isinstance(value, str) and (
                    "No response data" in value or "会话" in value or "未连接" in value
                ):
                    last = value
                else:
                    return value
            log(f"exec retry {attempt + 1}/{tries}: {last[:120]}")
            try:
                self.tabs()  # re-sync the newly opened background tab session
            except Exception:
                pass
            time.sleep(1)
        raise RuntimeError(last)

    def click(self, tab: str, target: str) -> dict:
        return self.json("click", "--tab", tab, target)

    def close(self, tab: str) -> None:
        try:
            self.json("close", "--tab", tab, timeout=15)
        except Exception as exc:
            log(f"WARNING: could not close tab {tab}: {exc}")

    def wait_ready(self, tab: str, tries: int = 12) -> bool:
        """New background tabs need the daemon to sync their session first."""
        for i in range(tries):
            try:
                state = self.js(tab, "return document.readyState", tries=1)
                if state in ("complete", "interactive", "loading"):
                    return True
            except Exception:
                pass
            try:
                self.tabs()
            except Exception:
                pass
            if i == 4:
                # A daemon restart forces the extension to re-register sessions.
                try:
                    self.raw("restart", timeout=20)
                except Exception:
                    pass
            time.sleep(1)
        return False


def state_file(cfg: dict) -> Path:
    return Path(cfg.get("state_dir") or default_data_dir()) / "last_run.json"


def already_ran_today(cfg: dict) -> bool:
    try:
        data = json.loads(state_file(cfg).read_text(encoding="utf-8"))
        return data.get("date") == dt.date.today().isoformat()
    except (OSError, ValueError):
        return False


def mark_ran_today(cfg: dict, summary: dict) -> None:
    p = state_file(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "date": dt.date.today().isoformat(),
        "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
        **summary,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def summarise_tasks(text: str) -> str:
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    pairs = []
    for i, line in enumerate(lines):
        if line.startswith("浏览") and i + 2 < len(lines):
            m = lines[i + 2].strip()
            if m.startswith("(") and m.endswith(")"):
                pairs.append(f"{line}{m}")
    return "; ".join(pairs) if pairs else " / ".join(lines[:12])


def run(args, cfg: dict) -> dict:
    cli = Cli(find_cli(cfg.get("cli_path", "")), timeout=35)
    log(f"CLI: {cli.path}")
    cli.restart()

    conn = {}
    for attempt in range(1, 4):
        status = cli.json("status", timeout=25)
        conn = status.get("connection", {}) or {}
        log(f"status (try {attempt}): healthy={status.get('healthy')} "
            f"extension={conn.get('extension_connected')} tabs={conn.get('active_tabs')}")
        if conn.get("extension_connected"):
            break
        time.sleep(2)
    if not conn.get("extension_connected"):
        raise RuntimeError("Chrome extension bridge is not connected. Is Chrome running with the extension loaded?")

    created: list[str] = []
    summary = {"signin": "skipped", "tasks": "skipped", "balance": ""}

    try:
        # ---- 1. daily sign-in on My JD ---------------------------------
        home = cli.open_bg(cfg["home_url"])
        created.append(home)
        if not cli.wait_ready(home):
            raise RuntimeError("home.jd.com tab did not become ready")
        time.sleep(cfg["page_wait_seconds"])
        home_state = cli.js(home, HOME_PROBE) or {}
        log(f"home: logged_in={home_state.get('logged_in')} "
            f"signin_button={home_state.get('signin_text')!r} beans={home_state.get('balance')}")
        if not home_state.get("logged_in"):
            raise RuntimeError("JD login not detected (redirected to passport or no bean wallet). Log in once in Chrome.")

        if home_state.get("signin_text") and cfg.get("allow_signin"):
            if args.dry_run:
                log("DRY-RUN: would click 立即签到")
                summary["signin"] = "dry-run"
            else:
                result = cli.click(home, '[class*="_checkInBtn_"]')
                ok = bool(result.get("result", {}).get("operation", {}).get("success"))
                log(f"sign-in click: success={ok}")
                time.sleep(cfg["page_wait_seconds"])
                after = cli.js(home, HOME_PROBE) or {}
                log(f"after sign-in: balance={after.get('balance')} button={after.get('signin_text')!r}")
                summary["signin"] = "clicked" if ok else "click-failed"
        else:
            summary["signin"] = "already-done"
            log("sign-in already completed today")

        # ---- 2. hidden PC task centre on the search page ---------------
        search = cli.open_bg(cfg["search_url"])
        created.append(search)
        if not cli.wait_ready(search):
            raise RuntimeError("search.jd.com tab did not become ready")
        time.sleep(cfg["page_wait_seconds"])
        panel = cli.js(search, TASK_STATE) or {}
        if not panel.get("panel"):
            cli.click(search, '[class*="jdmcc-signin_pill"]')
            time.sleep(2)
            panel = cli.js(search, TASK_STATE) or {}
        log(f"task panel: present={panel.get('panel')} pending={panel.get('pending')} "
            f"done={panel.get('done')} pill={panel.get('pill')}")

        if not cfg.get("allow_browse_tasks"):
            log("browse tasks disabled by config")
        elif args.dry_run:
            log(f"DRY-RUN: would run up to {cfg['max_task_rounds']} browse rounds")
            log(f"DRY-RUN task list: {summarise_tasks(panel.get('text', ''))}")
            summary["tasks"] = "dry-run"
        elif not panel.get("pending"):
            log("no pending browse tasks today (state_all_done or tasks already completed)")
            summary["tasks"] = "none-pending"
        else:
            injected = cli.js(search, INJECT_VISIBILITY) or {}
            log(f"visibility injection: {injected}")
            rounds = min(int(cfg["max_task_rounds"]), 20)
            for i in range(1, rounds + 1):
                state = cli.js(search, TASK_STATE) or {}
                pending = int(state.get("pending") or 0)
                if pending == 0:
                    log(f"all browse tasks finished after {i - 1} round(s)")
                    break
                marked = cli.js(search, MARK_PENDING_BUTTON)
                if marked == "none":
                    log("no clickable pending button; stopping")
                    break
                cli.click(search, "#jd-auto-target")
                time.sleep(1)
                cli.js(search, FIRE_VISIBILITY)
                time.sleep(cfg["task_wait_seconds"])
                after = cli.js(search, TASK_STATE) or {}
                log(f"round {i}: [{marked}] pending {pending} -> {after.get('pending')}, "
                    f"done={after.get('done')}")
            final = cli.js(search, TASK_STATE) or {}
            summary["tasks"] = f"done={final.get('done')} pending={final.get('pending')}"
            log(f"final task state: {summary['tasks']}: {summarise_tasks(final.get('text', ''))}")

        # ---- 3. verify bean balance ------------------------------------
        bean_tab = cli.open_bg(cfg["bean_list_url"])
        created.append(bean_tab)
        if cli.wait_ready(bean_tab):
            time.sleep(cfg["page_wait_seconds"])
            bal = cli.js(bean_tab, BEAN_BALANCE) or {}
            summary["balance"] = bal.get("balance", "")
            log(f"bean list: balance={bal.get('balance')} +2 rows={bal.get('plus2_rows')} "
                f"title={bal.get('title')}")
        else:
            log("WARNING: bean list tab not ready, balance not verified")
    finally:
        if cfg.get("close_temp_tabs") and not args.no_cleanup:
            for tab in reversed(created):
                cli.close(tab)
                log(f"closed temp tab {tab}")
            log(f"cleanup complete; {len(created)} tab(s) closed")
        else:
            log(f"cleanup skipped; opened tabs: {','.join(created)}")

    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="JD daily beans automation (background only)")
    ap.add_argument("--config", help="path to config.json (default: skill root / config.json)")
    ap.add_argument("--dry-run", action="store_true", help="inspect only; no clicks, no state write")
    ap.add_argument("--force", action="store_true", help="ignore the once-per-day guard")
    ap.add_argument("--max-rounds", type=int, help="override max_task_rounds")
    ap.add_argument("--no-cleanup", action="store_true", help="keep the temporary tabs open")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.max_rounds:
        cfg["max_task_rounds"] = args.max_rounds

    log_dir = Path(cfg.get("log_dir") or default_data_dir() / "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    LOGGER["path"] = str(log_dir / f"{dt.date.today():%Y-%m-%d}.log")

    log("=" * 60)
    log(f"jd-daily-beans start (dry_run={args.dry_run}, force={args.force})")

    if not args.force and not args.dry_run and already_ran_today(cfg):
        log("already completed today; skipping (use --force to override)")
        return 0

    # JD refreshes its daily task list around midnight. Running at 00:00 can
    # read yesterday's "all done" panel and mark the new day done prematurely.
    # hourly triggers before `earliest_hour` are skipped WITHOUT writing state.
    earliest = int(cfg.get("earliest_hour") or 0)
    if not args.force and not args.dry_run and dt.datetime.now().hour < earliest:
        log(f"before earliest_hour={earliest}; skipping (state not marked)")
        return 0

    try:
        summary = run(args, cfg)
    except Exception as exc:
        log(f"FAILED: {type(exc).__name__}: {exc}")
        log("state NOT marked as done; the next hourly trigger will retry")
        return 1

    if not args.dry_run:
        mark_ran_today(cfg, summary)
        log(f"state marked done for {dt.date.today():%Y-%m-%d}")
    log(f"DONE {json.dumps(summary, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
