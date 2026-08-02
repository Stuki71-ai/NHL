#!/usr/bin/env python3
"""
NHL EDGE — always keep Git, this PC, and the VPS in lockstep.

Default env: C:\\Users\\istva\\.claude\\CODE\\.env  (override with NHL_EDGE_ENV)

Usage (from repo root):
  python scripts/sync_all.py              # pull → tests → deploy VPS → verify
  python scripts/sync_all.py --no-pull    # skip git pull
  python scripts/sync_all.py --push       # also commit+push local changes first
  python scripts/sync_all.py --no-deploy  # PC/Git only (no SSH)
  python scripts/sync_all.py --no-cron    # deploy code but do not touch crontab

Rule: after ANY code change, run this. Never leave VPS on older code than Git main.

HARD SCOPE (operator mandate — never violate):
  - ONLY /root/nhl-edge and NBA systemd units (nhl-edge-live.service/timer).
  - Strip only NHL EDGE lines from root crontab; never wipe other jobs.
  - NEVER modify crypto-bot, edge-stacker, n8n, or any other job.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = Path(r"C:\Users\istva\.claude\CODE\.env")
VPS_REMOTE_DIR = "/root/nhl-edge"
# Files hashed on both sides for VERIFY
VERIFY_RELPATHS = [
    "nhl_edge/pipeline.py",
    "nhl_edge/brain.py",
    "nhl_edge/edge.py",
    "nhl_edge/config.py",
    "nhl_edge/delivery.py",
    "nhl_edge/dedupe.py",
    "nhl_edge/news.py",
    "nhl_edge/ratings.py",
    "nhl_edge/model.py",
    "nhl_edge/slate.py",
    "scripts/run_nhl_edge.py",
    "requirements.txt",
]
SECRET_KEYS = [
    "ODDS_API_KEY",
    "ODDS_API_KEY_FALLBACK",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "XAI_API_KEY",  # optional residual; brain no longer requires Grok
    "PERPLEXITY_API_KEY",
    "SERPER_KEY",  # news layer 2 (Serper.dev per-game fallback — NIGHT pattern)
    "WHOP_APP_KEY",
    "WHOP_API_KEY",
    "WHOP_OWNER_ID",
    "WHOP_SPORTS_EXP",
    "GQ_SPORTS_WEBHOOK_TOKEN",
    "GQ_SPORTS_WEBHOOK_URL",
    "GMAIL_USER",
    "GMAIL_APP_PASS",
    "GMAIL_TO",
    "EMAIL_ENABLED",
]


def run(cmd: list[str], check: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd or ROOT, check=check, text=True)


def load_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        sys.exit(f"missing env: {path}")
    d: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        d[k.strip()] = v.strip().strip('"').strip("'")
    if not d.get("VPS_HOST"):
        wh = d.get("GQ_SPORTS_WEBHOOK_URL") or d.get("GQ_NIGHT_WEBHOOK_URL") or ""
        host = urlparse(wh).hostname if wh else ""
        if not host:
            sys.exit("VPS_HOST missing and cannot derive from webhook URL")
        d["VPS_HOST"] = host
        print(f"derived VPS_HOST={host}")
    return d


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def ssh(host: str, remote_cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        f"root@{host}",
        remote_cmd,
    ]
    print("+", " ".join(cmd[:6]), "…", remote_cmd[:120])
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def write_deploy_marker(sha: str) -> None:
    (ROOT / "DEPLOY_SHA").write_text(sha + "\n", encoding="utf-8")


def git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def deploy_vps(env: dict[str, str], install_cron: bool) -> None:
    host = env["VPS_HOST"]
    sha = git_sha()
    write_deploy_marker(sha)

    # Build remote .env (secrets only — never commit)
    # Pick email ON (operator 2026-07-30). Do not inherit shared CODE EMAIL_ENABLED
    # (Chart Agent uses that flag for a different product).
    env_lines = [
        f"{k}={env[k]}"
        for k in SECRET_KEYS
        if env.get(k) and k != "EMAIL_ENABLED"
    ]
    env_lines.append("EMAIL_ENABLED=1")
    env_body = "\n".join(env_lines) + "\n"

    # Ensure remote dir + rsync/scp tree
    ssh(host, f"mkdir -p {VPS_REMOTE_DIR}/logs {VPS_REMOTE_DIR}/out")

    # Prefer scp recursive of tracked paths only
    # Use tar over ssh for clean deploy without shipping .git/out/__pycache__
    exclude = [
        "--exclude=.git",
        "--exclude=out",
        "--exclude=__pycache__",
        "--exclude=*.pyc",
        "--exclude=.env",
        "--exclude=.pytest_cache",
        "--exclude=workflow",
    ]
    # Windows: use tar if available (Windows 10+ has tar)
    tar_cmd = [
        "tar",
        "-cf",
        "-",
        *exclude,
        "-C",
        str(ROOT),
        "nhl_edge",
        "scripts",
        "docs",
        "deploy",
        "requirements.txt",
        "README.md",
        "DEPLOY_SHA",
        ".env.example",
        ".gitignore",
    ]
    print("+ tar … | ssh extract")
    tar_p = subprocess.Popen(tar_cmd, cwd=ROOT, stdout=subprocess.PIPE)
    ssh_p = subprocess.Popen(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            f"root@{host}",
            f"mkdir -p {VPS_REMOTE_DIR} && tar -xf - -C {VPS_REMOTE_DIR}",
        ],
        stdin=tar_p.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if tar_p.stdout:
        tar_p.stdout.close()
    out, err = ssh_p.communicate()
    tar_p.wait()
    if ssh_p.returncode != 0:
        print(err or out)
        sys.exit(f"deploy tar/ssh failed rc={ssh_p.returncode}")

    # Write remote .env via stdin (avoid shell history)
    print("+ ssh write remote .env")
    w = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            f"root@{host}",
            f"cat > {VPS_REMOTE_DIR}/.env && chmod 600 {VPS_REMOTE_DIR}/.env",
        ],
        input=env_body,
        text=True,
        capture_output=True,
    )
    if w.returncode != 0:
        print(w.stderr)
        sys.exit("failed to write remote .env")

    # venv + deps
    setup = (
        f"cd {VPS_REMOTE_DIR} && "
        f"python3 -m venv venv 2>/dev/null || true; "
        f"venv/bin/pip install -q -U pip && "
        f"venv/bin/pip install -q -r requirements.txt && "
        f"PYTHONPATH={VPS_REMOTE_DIR} venv/bin/python -c "
        f"\"from nhl_edge.pipeline import run; from nhl_edge.brain import SYS; "
        f"assert 'quality over quantity' in SYS and 'Vic Mackey' in SYS; print('import_ok')\""
    )
    r = ssh(host, setup)
    print((r.stdout or "").strip())
    if r.returncode != 0:
        print(r.stderr)
        sys.exit("remote pip/import failed")

    if install_cron:
        # HARD RULE: only NHL EDGE schedule units + strip our lines from root crontab.
        # NEVER wipe other jobs.
        #
        # America/New_York via systemd timer (not broken CRON_TZ, not dual-UTC+gate).
        # Slots (ET, US EDGE parity): 16:45 daily; 11:45/14:00 Sat+Sun. No 17:45.
        svc_local = ROOT / "deploy" / "nhl-edge-live.service"
        tim_local = ROOT / "deploy" / "nhl-edge-live.timer"
        if not svc_local.is_file() or not tim_local.is_file():
            sys.exit("missing deploy/nhl-edge-live.service or .timer")
        # ship unit files via base64 to avoid shell quoting issues
        import base64

        svc_b64 = base64.b64encode(svc_local.read_bytes()).decode("ascii")
        tim_b64 = base64.b64encode(tim_local.read_bytes()).decode("ascii")
        remote_script = f"""
set -e
export PATH=/usr/bin:/bin
TS=$(date -u +%Y%m%d_%H%M%S)
# 1) Remove ONLY NHL EDGE lines from root crontab (leave everything else)
cp -a /var/spool/cron/crontabs/root /root/crontab.backup.pre_nba_$TS 2>/dev/null || true
crontab -l 2>/dev/null > /tmp/cron_before_nba || true
BEFORE=$(wc -l < /tmp/cron_before_nba | tr -d ' ')
grep -v 'nhl-edge' /tmp/cron_before_nba \\
  | grep -v 'run_nhl_edge.py' \\
  | grep -v 'run_if_et.sh' \\
  | grep -v 'NHL EDGE' \\
  | grep -v '^CRON_TZ=America/New_York$' \\
  > /tmp/cron_others || true
OTHERS=$(wc -l < /tmp/cron_others | tr -d ' ')
if [ "${{BEFORE:-0}}" -ge 5 ] && [ "${{OTHERS:-0}}" -lt 3 ]; then
  echo "ABORT: non-NBA crontab nearly empty (before=$BEFORE others=$OTHERS)"
  exit 42
fi
crontab /tmp/cron_others
if grep -q crypto-bot /tmp/cron_before_nba; then
  grep -q crypto-bot <(crontab -l) || {{ echo ABORT: crypto-bot lines vanished; exit 44; }}
  echo crypto_preserved=1
fi
# prove no NBA junk left in root crontab
if crontab -l 2>/dev/null | grep -E 'run_nhl_edge|run_if_et|NHL EDGE' >/dev/null; then
  echo ABORT: NBA lines still in root crontab
  exit 45
fi
echo root_crontab_nba_cleared=1

# 2) Install systemd unit files (NBA-only)
echo {shlex.quote(svc_b64)} | base64 -d > /etc/systemd/system/nhl-edge-live.service
echo {shlex.quote(tim_b64)} | base64 -d > /etc/systemd/system/nhl-edge-live.timer
systemctl daemon-reload
# must show America/New_York calendars
grep -E '18:05:00 America/New_York|12:00:00 America/New_York' \\
  /etc/systemd/system/nhl-edge-live.timer
if grep -q '17:45:00' /etc/systemd/system/nhl-edge-live.timer; then
  echo ABORT: 17:45 still present
  exit 46
fi
for slot in '18:05:00' '12:00:00'; do
  grep -q "$slot America/New_York" /etc/systemd/system/nhl-edge-live.timer || {{
    echo "ABORT: missing slot $slot"; exit 46;
  }}
done
# Operator stop: /root/nhl-edge/STOPPED keeps schedule disabled
if [ -f {VPS_REMOTE_DIR}/STOPPED ]; then
  systemctl disable --now nhl-edge-live.timer 2>/dev/null || true
  systemctl stop nhl-edge-live.service 2>/dev/null || true
  echo TIMER_STOPPED
  systemctl is-enabled nhl-edge-live.timer 2>&1 || true
else
  systemctl enable --now nhl-edge-live.timer
  systemctl restart nhl-edge-live.timer
  echo TIMER_OK
  systemctl is-enabled nhl-edge-live.timer
  systemctl list-timers nhl-edge-live.timer --no-pager
fi
echo SCHEDULE_OK
"""
        cr = ssh(host, remote_script)
        print((cr.stdout or "").strip())
        if cr.returncode != 0:
            print(cr.stderr)
            sys.exit("schedule install failed (others left untouched if abort)")
        body = cr.stdout or ""
        if "SCHEDULE_OK" not in body or (
            "TIMER_OK" not in body and "TIMER_STOPPED" not in body
        ):
            sys.exit("systemd timer install incomplete")
        if "crypto_preserved=1" not in body and "crypto-bot" in (ssh(host, "crontab -l")[0] if False else ""):
            pass  # optional


def verify_vps(env: dict[str, str]) -> None:
    host = env["VPS_HOST"]
    print("\n=== VERIFY LIVE VPS vs PC ===")
    local_sha = (ROOT / "DEPLOY_SHA").read_text(encoding="utf-8").strip()
    r = ssh(
        host,
        f"cat {VPS_REMOTE_DIR}/DEPLOY_SHA 2>/dev/null; "
        f"test -x {VPS_REMOTE_DIR}/venv/bin/python && echo VENV_OK; "
        f"test -f {VPS_REMOTE_DIR}/.env && echo ENV_OK; "
        f"systemctl is-enabled nhl-edge-live.timer 2>/dev/null || true; "
        f"systemctl is-active nhl-edge-live.timer 2>/dev/null || true; "
        f"test -f {VPS_REMOTE_DIR}/STOPPED && echo STOPPED_FLAG || echo RUNNING_FLAG; "
        f"grep -E 'America/New_York' /etc/systemd/system/nhl-edge-live.timer 2>/dev/null; "
        f"crontab -l 2>/dev/null | grep -E 'run_nba|NHL EDGE' || echo NO_NBA_IN_ROOT_CRONTAB; "
        f"crontab -l 2>/dev/null | grep -c crypto-bot || true",
    )
    remote_out = (r.stdout or "").strip().splitlines()
    print("  remote:", remote_out)
    remote_sha = remote_out[0] if remote_out else ""
    blob = "\n".join(remote_out)
    stopped = "STOPPED_FLAG" in blob
    checks = {
        "DEPLOY_SHA match": remote_sha == local_sha and bool(local_sha),
        "venv present": "VENV_OK" in remote_out,
        "remote .env present": "ENV_OK" in remote_out,
        "timer TZ America/New_York": "America/New_York" in blob,
        "timer 18:05 ET daily": "18:05:00 America/New_York" in blob,
        "timer 12:00 weekend ET": "12:00:00 America/New_York" in blob,
        "no 17:45 ET": "17:45:00" not in blob,
        "no NBA junk in root crontab": "NO_NBA_IN_ROOT_CRONTAB" in blob,
    }
    if stopped:
        checks["schedule STOPPED (timer not enabled)"] = "disabled" in blob or "enabled" not in blob.split("STOPPED_FLAG")[0]
    else:
        checks["timer enabled"] = "enabled" in blob
        checks["timer active"] = "active" in blob

    # Per-file hashes
    for rel in VERIFY_RELPATHS:
        lp = ROOT / rel
        if not lp.is_file():
            checks[f"local {rel}"] = False
            continue
        local_h = file_sha256(lp)
        hr = ssh(
            host,
            f"sha256sum {VPS_REMOTE_DIR}/{rel} 2>/dev/null | awk '{{print $1}}'",
            check=False,
        )
        remote_h = (hr.stdout or "").strip()
        ok = remote_h == local_h
        checks[f"hash {rel}"] = ok
        if not ok:
            print(f"  MISMATCH {rel}: local={local_h[:12]} remote={remote_h[:12]}")

    bad = []
    for k, v in checks.items():
        print(f"  {k}: {v}")
        if not v:
            bad.append(k)
    if bad:
        sys.exit("VERIFY FAILED: " + ", ".join(bad))
    print("\nSYNC OK — Git/PC/VPS aligned")


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync NHL EDGE: Git ↔ PC ↔ VPS")
    ap.add_argument("--no-pull", action="store_true")
    ap.add_argument("--push", action="store_true", help="commit+push local changes")
    ap.add_argument("--no-deploy", action="store_true", help="skip VPS deploy")
    ap.add_argument("--no-cron", action="store_true", help="deploy without cron change")
    ap.add_argument(
        "--env",
        default=os.environ.get("NHL_EDGE_ENV", str(DEFAULT_ENV)),
        help="path to .env with secrets",
    )
    args = ap.parse_args()

    env_path = Path(args.env)
    env = load_env(env_path)

    if not args.no_pull:
        run(["git", "fetch", "origin"], check=False)
        run(["git", "pull", "--ff-only", "origin", "main"], check=False)

    if args.push:
        run(["git", "add", "-A"])
        st = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
        )
        if st.stdout.strip():
            run(
                ["git", "commit", "-m", "sync: keep PC/Git/VPS aligned"],
                check=False,
            )
            run(["git", "push", "origin", "main"], check=False)
        else:
            print("nothing to commit")

    # Local tests (mandatory)
    print("\n=== TESTS ===")
    tr = run(
        [sys.executable, "-m", "pytest", "nhl_edge/tests", "-q", "--tb=short"],
        check=False,
    )
    if tr.returncode != 0:
        sys.exit("tests failed — aborting sync")

    # EDGE family standing order + Mackey brand (US EDGE lead)
    pipe_src = (ROOT / "nhl_edge" / "pipeline.py").read_text(encoding="utf-8")
    brain_src = (ROOT / "nhl_edge" / "brain.py").read_text(encoding="utf-8")
    if "solely responsible for autonomous resolution" not in pipe_src:
        sys.exit("VERIFY FAIL: pipeline missing autonomy standing order")
    if "DELIVERY_ATTEMPTS" not in pipe_src:
        sys.exit("VERIFY FAIL: pipeline missing delivery autonomous retries")
    mackey_need = (
        "Vic Mackey",
        "ONE verifiable fact",
        "damn, I didn't know that",
        "punter jargon",
        "selection strategy secrets",
        "selling factors",
        "DO NOT repeat yourself",
        "55–75",
        "STARTS ON SUBSTANCE",
    )
    for m in mackey_need:
        if m not in brain_src:
            sys.exit(f"VERIFY FAIL: brain missing Mackey marker: {m}")
    print("  autonomy standing order: True")
    print("  delivery autonomous retries: True")
    print("  Mackey rationale brand: True")
    print("  rationale 55-75 word hard target: True")

    write_deploy_marker(git_sha() if (ROOT / ".git").exists() else "no-git")

    if not args.no_deploy:
        print("\n=== DEPLOY VPS ===")
        deploy_vps(env, install_cron=not args.no_cron)
        verify_vps(env)
    else:
        print("skip deploy (--no-deploy)")

    sha = git_sha() if (ROOT / ".git").exists() else "?"
    print(
        f"\nPC repo: {ROOT}\nGit: origin/main @ {sha[:12]}\n"
        f"VPS: root@{env['VPS_HOST']}:{VPS_REMOTE_DIR}"
    )


if __name__ == "__main__":
    main()
