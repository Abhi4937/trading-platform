# Gemini CLI — Project Mandates

## Session Start Checklist (do this first, every session)
1. Read HANDOFF.md — who worked last, what changed, what's pending
2. Read docs/memories/current_state.md — active tasks and open issues
3. Read docs/memories/work_log_claude.md — what Claude did (avoid conflicts)
4. Then ask the user what they want to work on

## Handoff Protocol (end of every session)
1. Update HANDOFF.md
2. Update docs/memories/work_log_gemini.md
3. Update docs/memories/current_state.md
4. Tell the user: "Ready to hand off to Claude — HANDOFF.md is updated"

## Architecture & Coordination
- Always coordinate with Claude via the `docs/memories/` directory.
- Maintain consistency in Greeks/IV computations using the established hybrid Bisection-Newton solver.
- Keep the "Partial Updates" architecture as the current primary objective.
