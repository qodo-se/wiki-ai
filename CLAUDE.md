# wiki-ai

## Planning checkpoints

For multi-step or multi-session work, write the plan to a file under `.claude/projects/` (e.g. `.claude/projects/<short-task-name>.md`) instead of only leaving it in the conversation. This directory is gitignored — plans are local scratch state, not committed.

- Use a checkbox list for steps; check items off as they're completed.
- Update the file as the plan changes instead of drifting silently from what's written.
- At the start of a task, check whether a matching plan file already exists and resume from it rather than re-planning from scratch.
- Once a plan's task is completed and verified, delete its file rather than leaving it around — a completed plan is noise, not a checkpoint. If the task is abandoned, delete it too rather than letting stale plans accumulate. Only keep a file while its task is still active.
- These files are local-only checkpoints, not documentation — don't treat them as a substitute for commit messages, PR descriptions, or CLAUDE.md updates.

## Pre-commit review with Qodo

Before creating a git commit in this repo, review the local diff with the Qodo CLI and fix any real findings first.

- Preferred: invoke the `qodo-review` skill (`/qodo-review`) — it sends the local diff plus session context (what changed and why) to Qodo's review engine, which reduces false positives.
- Equivalent raw command: `qodo review` (reviews unpushed changes against `origin/main`).
  - Limit scope: `qodo review <pathspec...>` (e.g. `qodo review backend/`)
  - Quick check: `qodo review --fast`
  - Thorough check before an important push: `qodo review --deep`
  - Give it context for fewer false positives: `qodo review --ticket <url>` or keep `.qodo/session-context.json` updated (auto-attached on every run)

Workflow:
1. Stage/finish the change.
2. Run `qodo review` (or the `qodo-review` skill) on the diff.
3. Triage findings — fix genuine bugs, use judgment on nitpicks.
4. Only then create the commit.

Skip this step only for trivial, non-functional changes (e.g. docs typo fixes) where a review adds no value.
