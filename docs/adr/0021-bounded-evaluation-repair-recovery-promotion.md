# ADR-0021: Bounded Evaluation Repair, Recovery, and Promotion

Status: Accepted for implementation on 2026-09-10 as the sixth Phase 5.2 production slice.

## Context

A `REFINEMENT_REQUIRED` decision previously advanced the campaign counter but did not bind the next
prompt set to the failed evidence. A validated winner also lacked an explicit promotion record, and
an assessment interrupted between committed intent and result persistence required database-level
intervention. Those gaps could permit prompt drift, ambiguous recovery, or an informal winner choice.

## Decision

- A human must commit a repair plan for every iteration after the first. The plan binds the source
  batch, failure snapshot, target iteration, exact prompt variants, workflow version, and candidate
  revision. Execution accepts only that exact plan and remains subject to campaign iteration, prompt,
  candidate, and zero-cost ceilings.
- A human may promote only the controller-selected winner from a completed trusted assessment. The
  immutable record binds campaign, batch, candidate, artifact digest, workflow version, and candidate
  revision. Promotion grants no merge, deployment, publication, provider-qualification, or permission
  authority.
- Recovery is explicit and human-only. Interrupted local validator work is conservatively recorded as
  failed evidence. Review intent known not to have run is marked failed. A review interrupted after
  dispatch becomes `UNKNOWN` and still requires the separate mark-failed reconciliation; it is never
  retried blindly.
- Repair, recovery, and promotion commands are replay-safe and visible through existing campaign and
  assessment read models. Terminal workflows cannot accept new evaluation mutations.

## Consequences

The refinement loop is now bounded and traceable, and a promoted result is content- and
revision-addressed. Conservative recovery may discard useful work, but it does not fabricate success
or repeat an ambiguous side effect. Real provider execution and downstream protected-PR proof remain
separate opt-in acceptance exercises.
