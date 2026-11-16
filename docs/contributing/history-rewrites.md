---
title: Git history rewrites
type: maintenance-record
created: 2026-06-18
owner: sky
tags: [contributing, history, security, neutrality]
---

# Git history rewrites

This project is open-source and **brand-neutral** (see
[`../../CLAUDE.md`](../../CLAUDE.md) §1 and [`../contributing.md`](../contributing.md)).
Banned identifiers — employer/brand tokens, a specific customer SoC model,
vendor SDK names, internal private-network (RFC1918) IPs, absolute home-directory
paths, and an internal short developer handle — must never appear in tracked
content. The guard `scripts/check_sensitive_words.sh` enforces this on every
commit.

When such an identifier leaks and is later removed from the working tree, the
**old versions still live in git history** — anyone browsing an old commit on
GitHub can still see them. Deleting them from the current tree is not enough;
the history itself has to be rewritten.

This file is the standing record of every history rewrite, so future
contributors know it happened and can coordinate before doing another.

## Policy (CLAUDE.md §5)

- A history rewrite is a **destructive, force-pushed** operation. It is only
  done to remove leaked sensitive content, never for cosmetic reasons.
- **Never force-push unilaterally.** Coordinate with the maintainer first.
- Always keep a **full local backup mirror** of the pre-rewrite history outside
  the working tree before force-pushing, so a bad rewrite can be rolled back.
- A rewrite is only safe to force-push when there are **no forks and no other
  active contributors** whose history would be broken. Verify this first.
- Note the limit: a rewrite stops *future* exposure (browsing, search indexing,
  fresh clones). Content already cloned or cached elsewhere cannot be recalled.

## Record

### Rewrite #4 — 2026-11-16 (before the first push of the merged history)

- **Why**: the project moved to this repository (`devicehands`) and the full
  history of the former repository was merged in. Before that history was
  pushed here for the first time, a scan of **every file version in history**
  (not only the current tree) found real identifiers that had been allowed on
  purpose in the rule-defining files (`CLAUDE.md`, `CONTRIBUTING.md`, the guard
  script, the pre-commit config, the auditor agent) because they *defined* the
  banned list: the company name, a customer SoC model, an internal handle, a
  home-directory path, two internal hosts and two internal subnets. One mock-up
  page carried a real LAN address, three commit messages quoted removed values,
  and one commit was authored with a laptop host name as the e-mail domain.
  The maintainer's rule: the public repository carries no local information;
  real values live only in a private repository.
- **Tool**: `git filter-repo` on a throwaway mirror clone of the unpushed
  history, in one pass: `--replace-text` for file contents (including escaped
  and `\b`-prefixed spellings found by a second, looser scan), `--replace-message`
  for the three messages, `--mailmap` for the author e-mail. Categories:
  - company / brand tokens → `vendor`, `com.example`, `example.com`
  - the customer SoC model → `arm-soc`; vendor SDK names → `soc-sdk`
  - the internal handle → `dev`; home-directory paths → `~`
  - internal hosts and subnets → documentation addresses (`192.0.2.x`,
    `198.51.100.x`)
  - the laptop host name → `laptop`; that commit's author → the public
    maintainer address
- **Before → after**: `main` moved from `43bfe0e` to `379268b`. 559 of 585
  commits changed hash; the 26 that kept theirs include this repository's
  first commit `53fece8`, the only commit already on `origin`, so the push was
  an ordinary fast-forward. Abbreviated hashes inside commit messages were
  rewritten by the tool; the 24 references in tracked files were updated in the
  commit that adds this entry.
- **Guard changed in the same commit**: the real values no longer live in this
  repository. `scripts/check_sensitive_words.sh` keeps only generic patterns
  and reads the real ones from a private word list
  (`$DHANDS_SENSITIVE_WORDS_FILE`); `CLAUDE.md` and the other rule files now
  name categories, not values.
- **Safety**: nothing of this history had been pushed to this repository; a
  full bundle of the pre-rewrite repository (all 13 refs) was kept outside it.
- **Verification**:
  - the private word list over all 10 681 blobs, all messages and all
    identities of the rewritten history: 0 hits apart from case-insensitive
    substrings inside third-party minified bundles and compressed image / font
    bytes (the same kind of coincidence noted in rewrite #3); the same scan of
    the original history: 121 blob hits and 8 message / identity hits;
  - a second scan with looser spellings (escaped dots, separators): 0 real hits
    after, 111 + 8 on the original;
  - at the tip, only the six rule / mock-up files differ from the original;
  - full test suite passes; the guard self-test passes and, run with the
    private list against the original tree, reports the five offending files.
- **Not done**: the former repository was **not** rewritten; its history and
  current tree still carry these values.

### Rewrite #3 — 2026-06-18 (neutrality scrub of historical blobs)

- **Why**: the current tree was already clean, but earlier commits still carried
  banned identifiers in historical versions of several tracked files (most
  heavily a long-lived notes file). Tracked internally as finding `SEC-1`.
- **Tool**: `git filter-repo --replace-text` (run on a throwaway clone, not the
  working repo).
- **Substitution categories** (literal banned strings intentionally omitted from
  this public record; each maps a leaked category to a neutral form):
  - company / brand tokens → `vendor`
  - product package prefix → `com.example`
  - a specific customer SoC model identifier → `arm-soc`
  - vendor SDK names → `soc-sdk`
  - absolute home-directory paths → `~`
  - an internal short developer handle → `dev`
    (the public maintainer handle `skyzhangbinghua` is preserved — it is a
    legitimate attribution identifier)
  - internal private-network IPs → `<llm-host>` / `<internal-ip>`
- **Before → after**: `main` moved from `e78da8c` to `d5d3077` (a fully divergent
  chain — every commit hash from the first scrubbed commit onward changed, which
  is why a force-push was required).
- **Restore commit**: the rewrite was followed by one commit restoring the
  rule-defining files (allowlisted by the guard) and the built site assets, so
  the final tree matches the pre-rewrite tree exactly.
- **Safety**: 0 forks, no other active contributors → force-push broke no one. A
  full backup mirror of the pre-rewrite history was retained outside the repo.
- **Verification**:
  - rewritten tree at `HEAD` byte-identical to the pre-rewrite tree;
  - guard `scripts/check_sensitive_words.sh --all` exits 0 on the tree;
  - full-history scan across all commits finds **0** real-source hits (the only
    regex match is a coincidental case-insensitive substring inside a
    third-party minified bundle, `docs/app/assets/xterm-*.js` — part of a
    camelCase option name in the library, not a brand token);
  - commit count intact;
  - **post-push** re-check: a fresh clone from `origin` re-ran the full-history
    scan → 0 real-source hits.

### Rewrites #1 and #2 — earlier (leaked-content removal)

Two earlier history rewrites removed leaked content before this record was
started. No detailed change log was kept for them at the time; they are noted
here for completeness per CLAUDE.md §5. From rewrite #3 onward every rewrite is
logged in this file.
