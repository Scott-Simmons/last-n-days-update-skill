# last-n-days-update-skill

A Claude Code skill for drafting status updates by analyzing your GitHub activity over the last N days.

## What it does

Pulls your authored PRs, reviews given, and issue/PR comments from the last N days via `gh`, cross-references a previous update to avoid double-counting, and emits Slack-formatted output ready to paste into a thread.

## Install

Symlink or copy `SKILL.md` into your Claude Code skills directory (e.g. `~/.claude/skills/last-n-days-update.md`), and make sure `scripts/pull_activity.py` is on a path the skill can reach.

```sh
ln -s "$PWD/SKILL.md" ~/.claude/skills/last-n-days-update.md
```

## Usage

Once installed, ask Claude something like:

> Draft my weekly update — last 7 days, cross-reference https://slack.com/.../p1776... so we don't repeat anything

Claude will:
1. Run `scripts/pull_activity.py --days 7 --user $USER --repos owner/repo1,owner/repo2`
2. Read the prior Slack thread (via the Slack MCP if available)
3. Categorise activity by theme
4. Flag open PRs needing review as blockers
5. Show you the draft for approval before posting

## Standalone use

You can also run the script directly:

```sh
./scripts/pull_activity.py --days 7 --user Scott-Simmons --repos UKGovernmentBEIS/inspect_evals,UKGovernmentBEIS/inspect_ai
```

Outputs JSON suitable for piping into other tools.

## Requirements

- `gh` CLI authenticated (`gh auth status`)
- Python 3.10+
- Git (for the `--local-repos` flag)
- (Optional) Slack MCP configured in Claude Code for cross-referencing prior updates
