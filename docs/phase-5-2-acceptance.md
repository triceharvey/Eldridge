# Phase 5.2 Multi-Model Evaluation Core Acceptance

## Outcome

The first Phase 5.2 slice implements the deterministic decision core for governed multi-model
evaluation and bounded refinement. It is deliberately side-effect free: it neither calls providers
nor executes model output. This separates selection policy from external calls and makes the same
evidence replay to the same decision.

## Verified controls

| Control | Evidence |
|---|---|
| Eligibility remains upstream | The evaluator accepts candidate evidence only; routing and activation still decide which providers may run |
| Deterministic validation | Every configured check must be present and pass before a candidate is rankable |
| No self-grading | The contract has no model-provided quality score; it consumes controller-derived routing and validation evidence |
| Stable selection | Arrival order cannot change the winner; score, cost, latency, and stable identities define ordering |
| Bounded fan-out | Unique provider/model candidates and prompt variants have explicit ceilings |
| Bounded refinement | Iterations are capped at five and return either a next iteration or terminal exhaustion |
| Cost containment | Aggregate prior and current costs cannot exceed the campaign ceiling; USD 0 campaigns admit only zero-cost evidence |
| Review diversity | High and critical risk require two distinct passed reviews outside the producer family |
| Artifact integrity | Candidate outputs, checks, and reviews use full SHA-256 evidence digests |
| No privilege promotion | A winner carries no merge, deployment, publication, canon, or human-approval authority |

## Current boundary

The engine currently evaluates one externally assembled batch at a time. Durable campaign tables,
authorized API commands, provider fan-out, revision-bound output storage, retry scheduling, and final
artifact promotion are not yet connected. Those are the next Phase 5.2 slice; this acceptance record
must not be used to imply that production orchestration is complete.
