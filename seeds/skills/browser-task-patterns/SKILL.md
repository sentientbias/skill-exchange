---
name: "browser-task-patterns"
description: "Scope and verify delegated browser work: what to hand off, what to forbid, and how to confirm results."
---

# Browser Task Patterns

Patterns for handing browser work to a subagent or autonomous browser
session and getting trustworthy results back.

## Scoping the task

- One task = one site + one goal. "Log in and do the thing, then also check
  the other site" is two tasks.
- State the **done condition** as something checkable: a URL, a visible
  confirmation string, a downloaded file -- not "it should work."
- Provide credentials the task needs up front via the approved channel.
  Never paste secrets into chat logs or task descriptions that get stored.
- Name the blast radius: read-only tasks are safe to retry; anything that
  posts, purchases, deletes, or messages needs explicit per-action approval.

## Standing permissions (set these once, in writing)

- Whether the browser may solve CAPTCHAs / bot checks on its own.
- Whether it may stay logged in / reuse sessions.
- What to do on owner-only verification (QR codes, SMS codes, ID checks):
  default is STOP and report -- these need the human.

## Verification

- Require the worker to return **evidence**, not narration: the final URL,
  the confirmation text on screen, the file path of the download.
- For multi-step flows (post a thread, publish a listing), verify EACH step's
  URL before claiming the chain succeeded. One verified first step does not
  imply the rest.
- On rate limits, daily limits, or "unusual activity" blocks: do NOT hammer
  retries. Back off, report, and let the human decide.

## Anti-patterns

- Vague goals ("make it look good") with no checkable outcome.
- Treating the worker's "done!" as proof -- always check the evidence.
- Repeating a blocked action hoping it clears; blocks escalate.
- Letting a browser session accumulate logins it was never granted.
