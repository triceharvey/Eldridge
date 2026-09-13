# ADR-0035: Bind Test Planning to Implementation Evidence

## Status

Accepted on 2026-09-13.

## Context

The first live run after ADR-0034 successfully committed the implementation provider's two typed
file writes to an isolated candidate revision. The subsequent test provider received the revision
identifier and workflow objective, but not the implementation output containing those writes. It
therefore reported that the files did not exist and returned a substantive negative decision before
the controller could execute its proposed compile and unit-test tools.

A test agent cannot make a useful candidate-specific decision from a revision identifier alone. The
controller already digest-binds the plan to architecture review and the implementation output to
security and code review. Test planning needs the same bounded evidence without granting repository
or command authority to the model.

## Decision

Treat `TEST` as an independent consumer of the successful `IMPLEMENT` attempt. Its provider request
includes the exact structured implementation output and the SHA-256 digest of its canonical JSON,
using the existing one-MiB evidence ceiling. Routing also treats testing as review work, preserving
cross-provider diversity when an eligible independent provider is available.

The model still only proposes typed test tools. The controller checks the proposal, creates a
candidate-revision worktree, and runs the compile and unit-test requests in the networkless,
read-only-root sandbox. Model context does not grant filesystem writes, host command execution,
approval, or merge authority.

## Consequences

- Test decisions are grounded in the same implementation artifact that produced the candidate.
- The implementation output and its digest become durable provenance for the test request.
- Independent provider routing now applies to test planning as well as review stages.
- Executable sandbox evidence remains required; model assertion alone cannot prove a test passed.
- A structurally valid negative test decision remains terminal and preserved under ADR-0034.

## Alternatives Rejected

- Let the test model infer repository contents from a revision identifier: produced a false absence
  finding in the live exercise.
- Give the model direct filesystem or command access: bypasses the typed-tool and sandbox boundary.
- Ignore a negative model decision and run tools anyway: weakens the explicit-review semantics from
  ADR-0034 and creates contradictory evidence.
- Embed an unrestricted repository snapshot: unnecessarily expands prompt data and trust surface.
