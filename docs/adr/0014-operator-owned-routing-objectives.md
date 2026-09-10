# ADR 0014: Operator-Owned Routing Objectives

## Status

Accepted for implementation on 2026-09-09 as the first Phase 5 usability slice.

## Context

The capability router previously used one fixed quality score with a small cost bonus. That was deterministic but did not let the operator express whether a particular Eldridge runtime should favor validated quality, responsiveness, or frugality. G0DM0D3's public design makes a useful general point: inference-time selection is more understandable when its objective and component scores are explicit. Eldridge needs that control without allowing an optimization preference to weaken security policy.

## Decision

Add fixed `BALANCED`, `QUALITY`, `SPEED`, and `FRUGAL` objectives under the versioned `routing-objectives/v1` profile. Configure one objective at process startup with `CONTROL_PLANE_ROUTING_OBJECTIVE`; default to `BALANCED`.

The router first applies all existing eligibility rules. It then calculates bounded quality, cost, and latency utilities for eligible candidates and applies the selected objective's fixed weights. Each routing record stores the objective, profile version, total score, and component utilities. Missing latency evidence receives a neutral utility, while observed latency is reliability-adjusted so rapid failures do not appear fast in a useful sense. Provider evidence remains partitioned by capability and exact model/profile version.

## Consequences

- An operator can choose a plain-language optimization goal without editing scoring code.
- The same inputs and policy versions reproduce the same result.
- Auditors can explain why one eligible provider outranked another.
- A quality or speed preference cannot authorize external egress, sensitive data, higher risk, excess cost, an unhealthy provider, or an unqualified provider.
- A changed objective affects future attempts only; historical decisions retain their recorded objective and scores.
- The presets are not claims that a provider is objectively best. Their utilities depend on measured, bounded evidence.

## Alternatives

- **Accept arbitrary user-supplied weights:** rejected because unbounded per-request weights are harder to review, compare, and audit.
- **Race every available model:** rejected as a default because it multiplies cost, egress, correlated exposure, and evaluation work.
- **Let a model select its own objective:** rejected because the governed component cannot define the criteria used to trust it.
- **Optimize before policy filtering:** rejected because a high score must never rescue an ineligible provider.
