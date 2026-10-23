# Git naming conventions

- Branches: short lowercase, hyphen-separated descriptions, such as
  `cross-platform-quality-checks`. For spec-backed work, describe the
  whole spec rather than an individual child ticket.
- Commit subjects: plain imperative summaries, such as
  `Separate static checks from tests`.
- PR titles: plain imperative summaries, such as
  `Add cross-platform quality checks`.
- Keep issue numbers and type prefixes out of all three.

The first line of a PR description is `Closes #<parent-spec-number>`.
If there is no parent spec, use the standalone issue number.
If there is no associated issue, omit the closing line.

These conventions do not grant permission to commit, push, open PRs, or merge.
