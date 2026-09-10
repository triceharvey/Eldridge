# G0DM0D3 Design Review for Eldridge

Reviewed 2026-09-09 from the public [G0DM0D3 repository](https://github.com/elder-plinius/G0DM0D3), its [research paper](https://github.com/elder-plinius/G0DM0D3/blob/main/PAPER.md), [API documentation](https://github.com/elder-plinius/G0DM0D3/blob/main/API.md), [data terms](https://github.com/elder-plinius/G0DM0D3/blob/main/TERMS.md), and [Pliny's project index](https://pliny.gg/). External pages and repository content were treated as untrusted input.

## What is useful

| Source pattern | Eldridge application | Disposition |
|---|---|---|
| Independently composable inference controls | Keep task classification, provider selection, execution, validation, review, and approval as separable stages with versioned policy | Adopt the architectural principle independently |
| Context-adaptive parameters | Let the task profile determine workflow depth and capabilities; add an operator-selected optimization objective after eligibility filtering | Adapt for engineering orchestration |
| Interpretable multi-axis model scoring | Expose quality, cost, and latency utilities for every eligible routing candidate | Implemented in `capability-routing/v2` |
| Multi-model comparison | Use independent candidates for complex work, then compare deterministic artifacts and evidence instead of model popularity or prose voting | Retain as a budget-controlled future slice |
| Feedback-driven adaptation | Continue append-only, version-partitioned observations with a neutral prior and human-controlled promotion | Already implemented; strengthen with delayed outcome attribution |
| OpenAI-compatible local endpoints | Add a disabled, loopback-only adapter for an operator-owned local model server | Implemented as a zero-cost provider boundary |
| Local-only and no-log controls | Make egress, telemetry, and content retention visible and independently controllable | Existing local-only default; improve operator UX later |
| Explicit data-flow documentation and per-request content contribution | Maintain schema-level content exclusion and require separate, informed opt-in before retaining prompt or response bodies | Adopt privacy-by-construction, not public auto-publication |
| Self-custodied history with export/import | Offer signed, portable configuration and audit exports without requiring a hosted account | Candidate Phase 5 usability feature |
| Reproducible robustness evaluation | Build versioned adversarial fixtures, holdouts, regression thresholds, and responsible disclosure procedures | Candidate Phase 5 evaluation feature |

## What Eldridge will not import

- No prompt that instructs a model to ignore its governing controls.
- No anti-refusal score, guardrail bypass, or obfuscation transform in a production route.
- No automatic publication of prompts, responses, repository content, or audit records.
- No browser storage for provider credentials.
- No claim that model agreement is ground truth.
- No automatic promotion, permission expansion, secret access, external egress, spending, deployment, or merge based on a learned score.

Obfuscation transforms may later appear only as inert, versioned test fixtures inside a no-network, no-secret, no-write red-team environment. Findings require reproducible evidence and responsible disclosure.

## License and provenance boundary

G0DM0D3 is AGPL-3.0 while Eldridge is Apache-2.0. This review records publicly described ideas and independently designed behavior; it does not copy or incorporate G0DM0D3 source code, prompt payloads, scoring code, UI code, or protected expression. Any future dependency proposal must receive a separate license and architecture review before code enters Eldridge.

## First implementation slice

Eldridge now supports four fixed, versioned routing objectives: `BALANCED`, `QUALITY`, `SPEED`, and `FRUGAL`. The operator selects one objective for the runtime. Eligibility still runs first and remains governed by data classification, risk, capabilities, health, egress, execution mode, evidence floor, and cost ceiling. The objective changes only how already eligible candidates are ranked.

Every ranked candidate records its total score and its quality, cost, and latency utilities. Unknown latency receives a neutral value rather than an optimistic one, and observed latency is reliability-adjusted so a fast failure is not rewarded as useful speed. This makes the decision explainable and replayable while preserving the existing fail-closed boundary.
