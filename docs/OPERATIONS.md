# NHL EDGE — Operations

> **DORMANT BY DESIGN (built 2026-08-01)** — timer disabled + `STOPPED` flag present until the
> 2026-27 season (~Oct 7). Activation is a one-command operator decision (see **Activation** below).

## Always sync (Git ↔ PC ↔ VPS)

```bash
cd C:\Users\istva\.claude\CODE\NHL-EDGE
python scripts/sync_all.py --push   # after any change
```

- **Git:** https://github.com/Stuki71-ai/NHL  
- **PC:** this repo  
- **VPS:** `root@<VPS_HOST>:/root/nhl-edge`  
- **Schedule (America/New_York):**

  | When (ET) | Role |
  |---|---|
  | **18:05 ET** every day | evening slate — after afternoon goalie confirmations, before 19:00 pucks |
  | **12:00 ET** Sat+Sun | weekend matinees |

  NHL-native slots (goalie confirmations are the anchor).

  **One customer proposal per ET day (HARD):** after any successful ship
  (grader claim + email/Whop) for that `date_et`, later weekend slots stay silent —
  no second email/Whop and no extra sheet claims.

  Implemented as **systemd timer** `nhl-edge-live.timer` with  
  `OnCalendar=… America/New_York` (not root crontab / not CRON_TZ hacks).  
  Units: `deploy/nhl-edge-live.service` + `.timer`.  
  Live slate = all remaining games on that ET game day.

- **Stop / resume schedule (VPS):**
  - Stop: `systemctl disable --now nhl-edge-live.timer` and `touch /root/nhl-edge/STOPPED`  
    (`sync_all` will not re-enable the timer while `STOPPED` exists.)
  - Resume: `rm /root/nhl-edge/STOPPED` then `systemctl enable --now nhl-edge-live.timer`  
    (or `python scripts/sync_all.py` after removing `STOPPED`).

- **EDGE-WATCHDOG (+5 min):** family watchdog at `/root/edge-watchdog` re-checks every EDGE slot five minutes later and heals once if the run is missing/failed. Skips a product when its live timer is disabled. NHL EDGE is REGISTERED THERE AT ACTIVATION, not before.

- Deploy writes remote `.env` from shared secrets (chmod 600). Never commit `.env`.

### HARD SCOPE — do not touch others

`sync_all` / operators / agents may **only** change:

- `/root/nhl-edge/**`  
- **NHL EDGE crontab lines** (`run_nhl_edge.py` + the `CRON_TZ=America/New_York` block immediately above them)

**Never** replace the full root crontab, never probe with a minimal crontab, never edit crypto-bot / edge-stacker / n8n / other jobs. Cron install aborts if non-NHL lines would be gutted.

## Credentials

Loaded from (first match, then local override):

1. `NHL_EDGE_ENV` path if set  
2. `C:\Users\istva\.claude\CODE\.env`  
3. `~/.claude/CODE/.env`  
4. `/root/.claude/CODE/.env`  
5. repo `.env` (always overrides when present)

Required for live:

| Key | Use |
|---|---|
| `ODDS_API_KEY` | The Odds API |
| `ANTHROPIC_API_KEY` | Claude Opus 5 primary composer |
| `OPENAI_API_KEY` | GPT-5.6 Sol fallback composer |
| `XAI_API_KEY` | grok-4.5 web+X news (primary) |
| `PERPLEXITY_API_KEY` | sonar-pro news (fallback; preflight-required floor) |
| `WHOP_APP_KEY` / `WHOP_OWNER_ID` / `WHOP_SPORTS_EXP` | Whop post |
| `GQ_SPORTS_WEBHOOK_URL` + `GQ_SPORTS_WEBHOOK_TOKEN` | grader claim |
| `GMAIL_USER` / `GMAIL_APP_PASS` / `GMAIL_TO` | email (if enabled) |

## Composer retry ladder

1. **3×** `claude-opus-5` with `thinking: {type: adaptive}` + `output_config.effort: max`  
   (Opus 5: adaptive thinking is default; we set it explicitly. Effort = depth control.)  
2. **3×** `gpt-5.6-sol` with effort **`high`** (fallback after Opus exhausted)  
3. Then **edge-rank** (no LLM)

Failures: timeout / HTTP / empty text / bad JSON / all picks rejected vs shortlist.  
Honest `[]` (no plays) is success — does **not** step down.

Facts logged: `composer_seconds`, tokens, attempt counts, `composer_effort_used`, `composer_model`, `composer_phase`.

## Commands

```bash
python scripts/run_nhl_edge.py --dry-run
python scripts/run_nhl_edge.py --live
python scripts/run_nhl_edge.py --date yesterday --dry-run
python scripts/grade_run.py --date YYYY-MM-DD
python -m pytest nhl_edge/tests -q
python scripts/sync_all.py --push
```

## Customer brand

| Surface | Brand |
|---|---|
| **Whop** title | **`US EDGE · …`** (same sports room as before) |
| **Email** subject | `NHL EDGE \| …` |
| Operator ntfy | `NHL EDGE …` |

## AI brain autonomy (standing order — EDGE family / US EDGE lead)

In case of any errors, the **AI brain is solely responsible for autonomous resolution** (honest `[]` / silence / shortlist-only / composer ladder → edge-rank — never invent teams, prices, or facts). Infrastructure retries transport failures before escalating.

**Notify via ntfy only if ALL THREE hold:**

1. **Production-critical** (subscriber delivery dead, pipeline cannot ship a live slate, secrets missing for live),  
2. **Could not be resolved after repeated autonomous attempts**, and  
3. **Resolution cannot wait** for the next scheduled slot.

| Situation | Behaviour |
|---|---|
| Honest no-picks / empty shortlist | Customers silence; private `Stuki71-EDGE` (not critical) |
| All same-day dupes | Silence; **no** ntfy |
| Composer fail | Ladder 3×high → 3×medium → 3×low → **edge-rank**; no ntfy if fallback ships |
| Delivery channel blip | **3 attempts** with backoff; critical ntfy only if still dead |
| Preflight missing secrets (live) | Critical ntfy once (cannot spend APIs / cannot wait) |
| Uncaught pipeline crash (live) | Critical ntfy once |

## ntfy

| Event | Topic | Title |
|---|---|---|
| Honest no picks / empty shortlist | `Stuki71-EDGE` | `NHL EDGE @ No picks for today` |
| Critical delivery/pipeline | `Stuki71-EDGE` | `NHL EDGE CRITICAL: …` |

## Activation (season start — operator decision, ~Oct 7)

1. `rm /root/nhl-edge/STOPPED`
2. `systemctl enable --now nhl-edge-live.timer` (or `python scripts/sync_all.py`)
3. Register NHL slots in **EDGE-WATCHDOG** (`/root/edge-watchdog`) — 18:05 ET daily + 12:00 ET Sat/Sun
4. First slate sanity: `python scripts/run_nhl_edge.py --dry-run` on the VPS, read the shortlist
5. Ratings are automatically 65% of last season at 0 games and regress in via `gp/(gp+12)` — no manual step

## Model gates

- Ratings: GF/g, GA/g → multiplicative off/def vs league GPG from ESPN standings (for NHL, `pointsFor`/`pointsAgainst` are GOAL totals; `gamesPlayed` present directly).
- Season blend: prev-season multiplier deviations shrunk ×0.65 (goalie/roster churn), current season `w = gp/(gp+12)`; 0 games (October) → 65% of last season. Team renames leave an inert previous-name orphan — harmless.
- Model: Poisson score matrix on λ = GPG · off · opp_def (HCA ×1.05/×0.97; B2B ×0.93/×1.05; λ clamp [1.2, 6.0]); ML incl. OT/SO via 50/50 regulation-tie split; totals/puck-line push mass split-renormed — literature constants, NOT fitted.
- B2B detection: ESPN scoreboard for yesterday (fail-open — fetch error means no B2B flags, never a blocked run).
- News: grok-4.5 `/v1/responses` + web_search/x_search, `tool_choice: required`, zero-search guard → sonar-pro fallback (never plain sonar); **confirmed starting goalies first**.
- Markets: ML ≥ 1.75, puck line ±1.5 ≥ 1.85 (graded as **Asian Handicap** — grader-native), totals ≥ 1.85; edge floor 2%; suspect > 30%.
- **Coin-flip:** `|expected goal margin| < 0.25` → no ML and no puck-line side (totals still allowed).
- **Slate:** all remaining pucks on the ET game day.
- Kickoff required (no TBD) before shortlist ship.
- **Goalie veto (composer):** backup goalie starting (or starter out/unconfirmed on a goalie-sensitive pick) → skip. The ratings model cannot see the goalie; the brain is the veto.
## Vic Mackey rationale rules (EDGE family brand — US EDGE lead)

Subscriber-facing rationales are brand identity and a primary selling point for paid recreational bettors. **Same verbatim bar as US EDGE** (in `nhl_edge/brain.py` `SYS`):

> For each pick, deliver a rationale in the harsh voice of Vic Mackey (The Shield): direct, focused on the exploitable edge. ONE verifiable fact per rationale (name, streak, record, injury, H2H, season stat or any similar) woven naturally into narrative — must make the reader go "damn, I didn't know that" - fully traceable. DO NOT reveal any parts of my selection strategy secrets and ABSOLUTELY NO punter jargon - these picks are for paid subscribers. Also DO NOT repeat yourself by the wording of the rationales. These entertaining rationales are important selling factors.

**Fact style:** data types a recreational bettor understands **at a glance** (not baby talk; not pipeline shop talk). `sync_all` + unit tests verify Mackey markers.

## Logs (VPS)

`/root/nhl-edge/logs/run.log`  
`/root/nhl-edge/out/run_YYYY-MM-DD.json`
