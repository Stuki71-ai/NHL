# NHL EDGE

Quantitative **NHL-only** betting edge service. Sibling of **NBA EDGE / NFL EDGE / MLB EDGE** (architecture) and **US EDGE** (delivery format + GQ Sports grader).

> **STATUS: BUILT DORMANT (2026-08-01).** Season starts ~Oct 7 — the timer is deployed
> **disabled** with the `STOPPED` flag present. Activation is an operator decision at season
> start (see OPERATIONS → Activation).

**GitHub:** https://github.com/Stuki71-ai/NHL  
**Local:** `C:\Users\istva\.claude\CODE\NHL-EDGE`  
**VPS:** `root@vmi…:/root/nhl-edge` — schedule **18:05 ET** daily + **12:00 ET** Sat+Sun (America/New_York timer)

## Always-in-sync rule

After **any** code change:

```bash
cd C:\Users\istva\.claude\CODE\NHL-EDGE
python scripts/sync_all.py --push
```

This keeps **Git main ↔ this PC ↔ VPS** aligned (pull → pytest → commit/push → scp deploy → hash verify → units).  
Never leave the VPS on older code than `origin/main`.

| Flag | Meaning |
|---|---|
| (default) | pull, test, deploy, verify, ensure units |
| `--push` | also commit+push local dirty tree first |
| `--no-deploy` | PC/Git only |
| `--no-cron` | deploy code without touching schedule units |
| `--no-pull` | skip `git pull` |

## Core idea

- **Poisson, not Gaussian:** ~3 goals/team makes NHL a score-matrix sport (family lineage: NIGHT/MLB EDGE). Multiplicative strength: `λ = league GPG × own offense mult × opponent defense mult`
- **Ratings:** GF/g and GA/g vs league from ESPN standings (for NHL, `pointsFor`/`pointsAgainst` are GOALS); previous season **shrunk to 65%** (goalie/roster churn), current blends in via `gp/(gp+12)` — October cold start solved
- **Situational:** home edge ×1.05/×0.97 (~+0.25 goals) and **back-to-back** ×0.93 own / ×1.05 opp (ESPN scoreboard detection, fail-open)
- **OT/SO handled:** regulation-tie mass splits into the two-way moneyline; totals/puck-line push mass split-renormed
- **Markets:** Moneyline ≥ 1.75 · **Puck line ±1.5** ≥ 1.85 (graded as Asian Handicap — grader-native) · Totals ≥ 1.85; edge ≥ 2%, ≤ 3 picks, 1/game
- **Coin-flip:** |expected goal margin| < 0.25 → no ML/puck-line side (totals stay)
- **News:** **grok-4.5 live web+X** — **confirmed starting goalies first**, then injuries (`tool_choice: required`, zero-search guard) → **sonar-pro** fallback — never plain sonar
- **Brain:** **claude-opus-5** (effort **max**, 3 tries) → **gpt-5.6-sol** (effort **high**) → **edge-rank**; the composer is the **goalie veto** (backup in net → skip unless the case survives)
- **Delivery:** Whop (sports exp, title stays `US EDGE`) + GQ Sports grader webhook (claim-first) + Gmail if enabled — email subject is `NHL EDGE`

Silence is valid. Honest no-picks → private ntfy `Stuki71-EDGE` title `NHL EDGE @ No picks for today` (operator-only).

## Why 18:05 ET

Starting goalies are confirmed through the afternoon (morning skate + beat writers). Running
at 18:05 — before the 19:00 pucks — gives the news layer maximum confirmed-goalie coverage;
the weekend 12:00 ET slot covers matinees. One customer proposal per ET day (pipeline gate).

## Quick start

```bash
cd C:\Users\istva\.claude\CODE\NHL-EDGE
pip install -r requirements.txt
# credentials: C:\Users\istva\.claude\CODE\.env  (auto-loaded; or local .env / NHL_EDGE_ENV)

python scripts/run_nhl_edge.py --dry-run
python scripts/run_nhl_edge.py --date 2026-04-10 --dry-run   # historical replay (last season)
python scripts/grade_run.py --date YYYY-MM-DD
python -m pytest nhl_edge/tests -q
```

## Layout

```
nhl_edge/          quant pipeline package
scripts/           run_nhl_edge, grade_run, sync_all
docs/              design.md, OPERATIONS.md
deploy/            nhl-edge-live.service / .timer (systemd, America/New_York)
```

See [docs/design.md](docs/design.md) and [docs/OPERATIONS.md](docs/OPERATIONS.md).
