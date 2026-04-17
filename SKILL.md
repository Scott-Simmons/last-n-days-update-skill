---
name: last-n-days-update
description: Generate a team status update by analyzing GitHub activity (PRs authored, reviews given, issue/PR comments) over the last N days. Cross-references a previous update (e.g. a Slack thread message) to avoid double-counting. Emits Slack-formatted output with hyperlinks. Use when the user asks for a weekly update, status update, standup writeup, or "what have I been doing".
---

# Last N Days Update

## When to use

The user wants to write a status update / standup post / weekly summary of their own work. Trigger phrases include:
- "write my weekly update"
- "what have I been working on"
- "draft a status update for the last N days"
- "I need to post in the standup thread"

## Inputs (ask if not provided)

- `days` (default 7): how far back to look
- `github_user` (default: current `gh` user): GitHub username whose activity to summarize
- `repos`: list of `owner/repo` slugs to scan. Default to repos the user has been active in (detect via `gh` if needed).
- `previous_update`: optional URL/text of a prior update (e.g. Slack permalink). If supplied, exclude anything already mentioned there.
- `format`: `slack` (default) | `markdown` | `plaintext`

## Steps

1. **Pull activity** by calling `scripts/pull_activity.py --days N --user USER --repos R1,R2 [--git-author EMAIL --local-repos PATH1,PATH2]`. The script returns JSON with five buckets:
   - `authored_prs`: PRs the user opened — with full body, commits (messages), additions/deletions/changedFiles, reviews received, and conversation comments
   - `authored_issues`: issues the user opened — title, body, state, labels
   - `reviews_given`: PRs the user reviewed — full review body, state (APPROVED/CHANGES_REQUESTED/COMMENTED), and every inline line-comment the user made
   - `issue_comments`: full comment bodies (not previews) the user made on issues and PR conversations
   - `local_commits`: per local repo path, commits by the user in the window, each with an `unpushed` flag. Requires `--git-author` and `--local-repos`.

2. **Verify currency**. Re-check the live state of any PRs you'll mention (open vs merged vs closed-not-merged). Don't trust stale snapshots — the script timestamps results, but a PR may have flipped state since it was queried. Closed-not-merged PRs are usually noise and should be omitted unless they were superseded by a still-open PR worth mentioning.

3. **Cross-reference prior update**. If `previous_update` is given:
   - Read the message via the Slack MCP `slack_read_thread` tool (or `WebFetch` if it's a URL the user pasted)
   - Extract the PR/issue numbers already mentioned
   - Exclude them from the new update unless there's a meaningful state change worth re-flagging (e.g. "previously WIP, now merged")

4. **Categorise** activity into themes. Don't enforce a fixed taxonomy — let the actual work suggest the buckets. Common ones:
   - Eval-specific work (group by eval name)
   - Infra / tooling
   - Docs / discoverability
   - Smoke testing / CI
   - Cross-repo work (e.g. upstream framework changes)
   - Reviews

5. **Identify blockers**. Open PRs the user authored where `reviewDecision == REVIEW_REQUIRED` and `isDraft == false` belong in section 3 as "reviews needed". Long-running design discussions on open PRs (lots of comments, no merge) are also blockers worth surfacing.

6. **Format**. See "Slack formatting" below. Emit three sections:
   - `(1)` Additions to "what I've been working on" — bold themed headers + bullets
   - `(2)` What's next — bullets
   - `(3)` Blockers / help needed — bullets, with reviews-needed PRs called out

7. **Disclaimer**. End with an italic line noting Claude generated it and what data sources were used. Be specific: "looked at git log, GitHub PRs, reviews, and comments from the past N days".

## Slack formatting (lessons learned)

- Use `*bold*` for section headers (single asterisk, not double).
- Use `•` for bullets (not `-` or `*` — those can be parsed as italic/bold).
- Use Slack link syntax: `<https://example.com|display text>`. Plain `#1442` references don't auto-link.
- **Avoid nested ordered lists** (`a.` / `i.` / `ii.`). Slack's API renderer mangles them. Flat bullets under bold headers render cleanly.
- Avoid horizontal rules (`---`) — they trigger `invalid_blocks` errors via the API.
- Em dashes (`—`) are fine.
- Vary the link text naturally rather than repeating "this PR" everywhere — e.g. `<url|the mlrc_bench fixes>`, `<url|configurable preset system>`.

## Posting

- **Always show the draft to the user first.** Never auto-post to Slack.
- After approval, post via `slack_send_message` with the right `channel_id` and `thread_ts`.
- If the user wants to fold into an existing message: there's no edit API. Either (a) they edit manually and you give them the text to paste, or (b) you post as a follow-up reply in the thread.

## Common pitfalls

- **Closed PRs are not merged PRs.** `state: CLOSED` with no `mergedAt` means abandoned/superseded — usually omit.
- **Draft PRs are work-in-progress.** Mention them in section (1) under their theme, not in section (3) as "needs review".
- **Don't double-count reviews.** If the previous update already listed a PR you reviewed, don't re-list it just because it later merged. Either skip it or fold the merge note into the original line.
- **Cross-repo work matters.** Activity in upstream/related repos (e.g. `inspect_ai` for `inspect_evals` work) is often the most interesting thing in the update.
