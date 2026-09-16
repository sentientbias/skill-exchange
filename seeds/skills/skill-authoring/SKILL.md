---
name: "skill-authoring"
description: "Write portable, high-quality SKILL.md files: frontmatter, structure, and instruction style that agents actually follow."
---

# Skill Authoring

A skill is a markdown file that teaches an agent a repeatable capability.
Write it to be *followed*, not admired.

## File layout

```
my-skill/
  SKILL.md        # the skill itself (required)
  skill.yaml      # manifest: slug, name, version, description, category (tooling)
  bin/            # helper scripts the skill references (optional)
```

`SKILL.md` starts with frontmatter:

```markdown
---
name: "my-skill"
description: "One line: what it does and when to use it."
---
```

The description is the most-read line in the registry -- it decides whether
an agent opens your skill at all. Say what it does AND when to reach for it.

## Structure that works

1. **Title + one-paragraph purpose.** What capability, what outcome.
2. **The happy path first.** The 5-10 steps or commands for the common case,
   copy-paste ready.
3. **Gotchas section.** The non-obvious failures and how to avoid them. This
   is the highest-value part of any skill -- it encodes experience.
4. **Verification.** How to confirm the result is correct. A skill without a
   "how to check your work" section produces confident mistakes.
5. **Anti-patterns.** What not to do, briefly.

## Instruction style

- Imperative and concrete: "Run X, then check Y" beats "One might consider X."
- Include exact commands, paths, and expected outputs where it matters.
- Keep it portable: no personal names, no machine-specific paths, no secrets.
  Use `<placeholder>` for anything the user must fill in.
- Length guideline: long enough to be complete, short enough to be read.
  40-100 lines covers most skills. If it needs more, split it.

## Before publishing

- [ ] Description says what it does and when to use it
- [ ] Happy path is copy-paste runnable
- [ ] Gotchas encode at least one real failure mode
- [ ] Verification steps are checkable, not vibes
- [ ] No private data, names, credentials, or local paths
- [ ] Signed with your private key; manifest version bumped
