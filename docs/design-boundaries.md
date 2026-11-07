# Design boundaries

This document records the approved target for the simplification in #75. It is a
design constraint, not a release-status claim: a behavior described here may
not be implemented yet. Check the current source and usage documentation before
describing a target behavior as available.

## Operating assumptions

- A repository, including its worktrees, has at most one active Loop run.
  Independent repositories remain independently usable. A small fail-fast
  overlap guard is appropriate; a queue or cross-run coordinator is not.
- Agents are cooperative but fallible, not adversarial. An agent that delegates
  work is responsible for joining it and finishing reporting before its
  harness exits. The Loop does not supervise detached work.
- Agent attestation is an explicit claim, not independent correctness
  verification or human approval. Manual finalization is a separate human
  action. See `GLOSSARY.md` for these terms.

## Exclusions

The target does not provide concurrent-run coordination, hostile same-user
tampering defense, an independent report-observation channel,
detached-worker/process-tree supervision, automatic retry, exact resume or
repair, or exact historical recovery. A single-run product boundary does not
forbid concurrency as an implementation technique; it excludes coordinating
multiple Loop runs in one repository and supporting unjoined workers.

## Changing a boundary

Before adding machinery or a regression requirement, classify the scenario
against these assumptions: is it a failure within the supported model, or does
it require one of the excluded guarantees? Surface conflicts to the owner.
Explicit owner approval is required before broadening a guarantee, including
when a reviewer describes an excluded scenario as a correctness bug.

Tests enforce the supported model; they do not enlarge it. Small safe writes
and the fail-fast overlap guard remain in scope. Tests for excluded concurrency,
tampering, or detached-worker guarantees require the same explicit approval.
