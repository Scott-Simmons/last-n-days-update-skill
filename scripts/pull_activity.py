#!/usr/bin/env python3
"""Deep pull of a user's GitHub + local git activity over the last N days.

Pulls:
- authored_prs: PRs the user opened, with body, commits, additions/deletions,
  reviews received, and top-level PR conversation comments
- authored_issues: issues the user opened, with body and state
- reviews_given: full review body, state (APPROVED / CHANGES_REQUESTED /
  COMMENTED), and every inline line-comment the user made in the review
- issue_comments: full comment body (not truncated) for comments the user
  made on issues or PR conversations
- local_commits: per local repo path, commits by the user in the window,
  with an `unpushed` flag detected via `git branch -r --contains`

Emits JSON to stdout.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def run(*args: str, cwd: str | None = None) -> str:
    result = subprocess.run(
        args, capture_output=True, text=True, check=True, cwd=cwd
    )
    return result.stdout


def gh(*args: str) -> str:
    return run("gh", *args)


def graphql(query: str) -> dict[str, Any]:
    return json.loads(gh("api", "graphql", "-f", f"query={query}"))


_PR_DEEP_FIELDS = [
    "number",
    "title",
    "body",
    "state",
    "isDraft",
    "mergedAt",
    "createdAt",
    "updatedAt",
    "reviewDecision",
    "url",
    "additions",
    "deletions",
    "changedFiles",
    "files",
    "commits",
    "reviews",
    "comments",
    "labels",
]


def authored_prs(user: str, repo: str, since: datetime, until: datetime) -> list[dict]:
    """Two-pass: list PRs cheaply, then fetch deep detail per recent PR.

    Single-pass with all fields hits GitHub's 500k-node GraphQL limit fast
    once body/files/commits/reviews/comments are all included.
    """
    raw = gh(
        "pr",
        "list",
        "--repo",
        repo,
        "--author",
        user,
        "--state",
        "all",
        "--limit",
        "500",
        "--json",
        "number,updatedAt,state",
    )
    summary = json.loads(raw)
    recent_numbers = [
        pr["number"]
        for pr in summary
        if since <= datetime.fromisoformat(pr["updatedAt"].replace("Z", "+00:00")) <= until
    ]
    out = []
    for number in recent_numbers:
        detail_raw = gh(
            "pr",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            ",".join(_PR_DEEP_FIELDS),
        )
        pr = json.loads(detail_raw)
        pr["repo"] = repo
        out.append(pr)
    return out


def authored_issues(user: str, since: datetime, until: datetime) -> list[dict]:
    search_query = (
        f"author:{user} is:issue "
        f"created:{since.date().isoformat()}..{until.date().isoformat()}"
    )
    query = """
    {
      search(query: "%s", type: ISSUE, first: 100) {
        nodes {
          ... on Issue {
            number
            title
            body
            state
            url
            createdAt
            closedAt
            repository { nameWithOwner }
            labels(first: 10) { nodes { name } }
          }
        }
      }
    }
    """ % search_query
    data = graphql(query)
    nodes = data["data"]["search"]["nodes"]
    out = []
    for n in nodes:
        if not n:
            continue
        out.append(
            {
                "number": n["number"],
                "title": n["title"],
                "body": n["body"],
                "state": n["state"],
                "url": n["url"],
                "createdAt": n["createdAt"],
                "closedAt": n["closedAt"],
                "repo": n["repository"]["nameWithOwner"],
                "labels": [l["name"] for l in n["labels"]["nodes"]],
            }
        )
    return out


def reviews_given(user: str, since: datetime, until: datetime) -> list[dict]:
    """Each review: body, state, PR context, and every inline line comment.

    Uses GraphQL contributionsCollection(from,to) so the server scopes to
    the window — avoids the global "newest 100" cap when the window is far
    in the past or the user reviews heavily.
    """
    out: list[dict] = []
    cursor = "null"
    since_iso = since.isoformat().replace("+00:00", "Z")
    until_iso = until.isoformat().replace("+00:00", "Z")
    while True:
        query = """
        {
          user(login: "%s") {
            contributionsCollection(from: "%s", to: "%s") {
              pullRequestReviewContributions(first: 100, after: %s) {
                pageInfo { hasNextPage endCursor }
                nodes {
                  occurredAt
                  pullRequestReview {
                    body state url
                    comments(first: 100) {
                      nodes { body path line url outdated createdAt }
                    }
                  }
                  pullRequest {
                    number title state isDraft url
                    repository { nameWithOwner }
                  }
                }
              }
            }
          }
        }
        """ % (user, since_iso, until_iso, cursor)
        data = graphql(query)
        conn = data["data"]["user"]["contributionsCollection"][
            "pullRequestReviewContributions"
        ]
        for n in conn["nodes"]:
            pr = n["pullRequest"]
            review = n["pullRequestReview"] or {}
            inline = (review.get("comments") or {}).get("nodes") or []
            out.append(
                {
                    "occurredAt": n["occurredAt"],
                    "pr_number": pr["number"],
                    "pr_title": pr["title"],
                    "pr_state": pr["state"],
                    "pr_isDraft": pr["isDraft"],
                    "pr_url": pr["url"],
                    "repo": pr["repository"]["nameWithOwner"],
                    "review_body": review.get("body", ""),
                    "review_state": review.get("state", ""),
                    "review_url": review.get("url", ""),
                    "inline_comments": [
                        {
                            "body": c["body"],
                            "path": c["path"],
                            "line": c.get("line"),
                            "url": c["url"],
                            "outdated": c["outdated"],
                            "createdAt": c["createdAt"],
                        }
                        for c in inline
                    ],
                }
            )
        page = conn["pageInfo"]
        if not page["hasNextPage"]:
            break
        cursor = '"%s"' % page["endCursor"]
    return out


def issue_comments(user: str, since: datetime, until: datetime) -> list[dict]:
    """Full comment bodies (not truncated) on issues and PR conversations.

    Paginates newest-first and stops once we walk past `since`.
    """
    out: list[dict] = []
    cursor = "null"
    while True:
        query = """
        {
          user(login: "%s") {
            issueComments(first: 100, after: %s, orderBy: {field: UPDATED_AT, direction: DESC}) {
              pageInfo { hasNextPage endCursor }
              nodes {
                createdAt updatedAt url body
                issue {
                  number title url state
                  repository { nameWithOwner }
                }
              }
            }
          }
        }
        """ % (user, cursor)
        data = graphql(query)
        conn = data["data"]["user"]["issueComments"]
        oldest_in_page: datetime | None = None
        for n in conn["nodes"]:
            created = datetime.fromisoformat(n["createdAt"].replace("Z", "+00:00"))
            if oldest_in_page is None or created < oldest_in_page:
                oldest_in_page = created
            if created < since or created > until:
                continue
            issue = n["issue"]
            out.append(
                {
                    "createdAt": n["createdAt"],
                    "updatedAt": n["updatedAt"],
                    "url": n["url"],
                    "body": n["body"],
                    "issue_number": issue["number"],
                    "issue_title": issue["title"],
                    "issue_state": issue["state"],
                    "issue_url": issue["url"],
                    "repo": issue["repository"]["nameWithOwner"],
                }
            )
        page = conn["pageInfo"]
        if not page["hasNextPage"]:
            break
        if oldest_in_page is not None and oldest_in_page < since:
            break
        cursor = '"%s"' % page["endCursor"]
    return out


def local_commits(
    git_author: str, repo_path: str, since: datetime, until: datetime
) -> list[dict]:
    """Commits by git_author in repo_path since cutoff, with unpushed flag.

    Uses `--all` so branches without upstream are included. `unpushed` is
    True when no remote branch contains the commit.
    """
    sep = "\x1f"  # unit separator
    end = "\x1e\n"  # record separator
    fmt = sep.join(["%H", "%an", "%ae", "%aI", "%s", "%b"]) + end
    try:
        raw = run(
            "git",
            "log",
            "--all",
            f"--author={git_author}",
            f"--since={since.isoformat()}",
            f"--until={until.isoformat()}",
            f"--pretty=format:{fmt}",
            cwd=repo_path,
        )
    except subprocess.CalledProcessError as e:
        return [{"error": f"git log failed in {repo_path}: {e.stderr}"}]

    commits = []
    for record in raw.split("\x1e\n"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split(sep)
        if len(parts) < 6:
            continue
        sha, name, email, iso_date, subject, body = parts[:6]
        try:
            branches_containing = run(
                "git", "branch", "-r", "--contains", sha, cwd=repo_path
            ).strip()
        except subprocess.CalledProcessError:
            branches_containing = ""
        unpushed = not branches_containing
        commits.append(
            {
                "sha": sha,
                "author_name": name,
                "author_email": email,
                "date": iso_date,
                "subject": subject,
                "body": body.rstrip(),
                "unpushed": unpushed,
                "remote_branches": [
                    b.strip().lstrip("* ").strip()
                    for b in branches_containing.splitlines()
                    if b.strip()
                ],
                "repo_path": repo_path,
            }
        )
    return commits


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=7)
    p.add_argument(
        "--since",
        help="Window start, YYYY-MM-DD UTC. Overrides --days.",
    )
    p.add_argument(
        "--until",
        help="Window end, YYYY-MM-DD UTC. Defaults to now.",
    )
    p.add_argument("--user", required=True, help="GitHub username")
    p.add_argument(
        "--repos",
        required=True,
        help="Comma-separated owner/repo slugs to scan for authored PRs",
    )
    p.add_argument(
        "--git-author",
        help=(
            "Author substring for `git log --author=...` (email or name). "
            "Required if --local-repos is set. Can be a regex/substring."
        ),
    )
    p.add_argument(
        "--local-repos",
        default="",
        help=(
            "Comma-separated local git repo paths to scan for commits (local "
            "and unpushed). Empty to skip."
        ),
    )
    args = p.parse_args()

    now = datetime.now(timezone.utc)
    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    else:
        since = now - timedelta(days=args.days)
    if args.until:
        until = datetime.fromisoformat(args.until).replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
    else:
        until = now
    repos = [r.strip() for r in args.repos.split(",") if r.strip()]
    local_paths = [p.strip() for p in args.local_repos.split(",") if p.strip()]

    output: dict[str, Any] = {
        "queried_at": now.isoformat(),
        "since": since.isoformat(),
        "until": until.isoformat(),
        "user": args.user,
        "repos_scanned": repos,
    }

    authored = []
    for repo in repos:
        authored.extend(authored_prs(args.user, repo, since, until))
    output["authored_prs"] = sorted(
        authored, key=lambda p: p["updatedAt"], reverse=True
    )
    output["authored_issues"] = authored_issues(args.user, since, until)
    output["reviews_given"] = reviews_given(args.user, since, until)
    output["issue_comments"] = issue_comments(args.user, since, until)

    if local_paths:
        if not args.git_author:
            print(
                "error: --git-author is required when --local-repos is set",
                file=sys.stderr,
            )
            return 2
        commits_by_repo = {}
        for path in local_paths:
            resolved = str(Path(path).expanduser().resolve())
            commits_by_repo[resolved] = local_commits(
                args.git_author, resolved, since, until
            )
        output["local_commits"] = commits_by_repo
    else:
        output["local_commits"] = {}

    json.dump(output, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
