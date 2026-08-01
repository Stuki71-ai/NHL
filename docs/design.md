# NHL EDGE — Design

**Brand:** NHL EDGE (email/ntfy) · `US EDGE` on Whop (same sports room)  
**Sibling of:** NBA/NFL/MLB EDGE (architecture), US EDGE (delivery/grader), NIGHT EDGE (Poisson lineage)  
**Status:** v1 Python service, built dormant 2026-08-01 — GitHub `Stuki71-ai/NHL`

## Thesis

At ~3 goals per team, NHL scoring is Poisson territory — the same score-matrix core that runs
NIGHT EDGE (soccer xG) and ran MLB EDGE, with multiplicative goal-rate strength. The single
dominant availability factor — **the starting goalie** — is deliberately NOT modeled: it is
handled by the news layer (confirmed starters first, from morning-skate and beat-writer
reporting) plus a hard composer veto, because goalie news on X beats any goalie-value model
we could maintain.

## Architecture (family-shaped)

```
systemd timer (ET)
    │
    ▼
[1] Slate          Odds API icehockey_nhl — h2h + spreads(±1.5 puck line) + totals
    │              (best price/side, consensus line for spread & total)
    ▼
[2] B2B            ESPN scoreboard (yesterday) → 2nd-night flags per team
    │
    ▼
[3] Ratings        GF/g, GA/g per team → multiplicative off/def vs league GPG
    │              (ESPN standings: for NHL, pointsFor/pointsAgainst are GOALS; gamesPlayed direct)
    │              prev season shrunk ×0.65, current blends in w = gp/(gp+12)
    ▼
[4] Model          λ_home = GPG · off_h · def_a · 1.05   λ_away = GPG · off_a · def_h · 0.97
    │              B2B: ×0.93 own / ×1.05 opp · λ clamped [1.2, 6.0]
    │              independent Poisson score matrix (truncated 12, renormed)
    │              ML incl. OT/SO: regulation-tie mass split 50/50
    │              totals & puck line: push mass split-renormed (family standard)
    ▼
[5] Edge calc      edge = model_p · price − 1
    │              floors: ML ≥ 1.75, puck line/total ≥ 1.85, min edge 2%, suspect > 30%
    │              coin-flip: |λ_h − λ_a| < 0.25 → sides blocked (totals stay)
    │              max 1 market/game; rank by win prob, then edge
    ▼
[6] News           grok-4.5 web+X (tool_choice required, zero-search guard):
    │              CONFIRMED STARTING GOALIES FIRST, injuries, line moves → sonar-pro fallback
    ▼
[7] Brain          claude-opus-5 @ max ×3 → gpt-5.6-sol @ high → edge-rank
    │              select ≤3 from shortlist only (exact key bind);
    │              GOALIE VETO: backup in net / starter out on a goalie-sensitive pick → skip
    ▼
[8] Delivery       grader claim FIRST (family dedupe) → Whop + email (if enabled)
```

Silence is valid: empty shortlist or brain `[]` → no customer delivery; private no-picks ntfy.

## Markets (v1)

- Moneyline including OT/SO (two-way), floor 1.75
- Puck line ±1.5 at the consensus line, graded as **Asian Handicap** (grader-native), floor 1.85
- Full-game totals (5.5/6/6.5 class) at the consensus line, floor 1.85

No period markets, no props, no live.

## AI roles

| Role | Model | Job |
|---|---|---|
| Research news | `grok-4.5` web+X → `sonar-pro` fallback | Confirmed goalies, injuries, one sharp fact per matchup |
| Pick composer | `claude-opus-5` @ max ×3 → `gpt-5.6-sol` @ high → edge-rank | Choose from **shortlist only**; goalie veto; never invent prices/teams |
| (Not used for fair odds) | — | Fair probs come from the Poisson model, not the LLM |

## Non-goals (v1)

- Goalie-value models / save-percentage adjustments (news + veto instead)
- Shot-based metrics (xG, Corsi, Fenwick) — goals rates + regression carry the season signal at this granularity
- Period markets, props, live
- Replacing NBA/NFL/MLB/US EDGE

## Schedule (America/New_York via systemd timer)

| OnCalendar | ET | Role |
|---|---|---|
| `*-*-* 18:05:00 America/New_York` | 18:05 ET daily | evening slate — after afternoon goalie confirmations, before 19:00 pucks |
| `Sat,Sun *-*-* 12:00:00 America/New_York` | 12:00 ET Sat+Sun | weekend matinees |

One customer proposal per ET day (pipeline gate). Live window: every unstarted game on the
current ET game day.

## Season boundary

October cold start: 0 games → ratings are 65% of last season's rate deviations (goalie/roster
churn), blending to current form via `gp/(gp+12)`. No manual seasonal maintenance. Team renames
(Utah class) leave an inert previous-name orphan in the table — harmless, current names match first.
