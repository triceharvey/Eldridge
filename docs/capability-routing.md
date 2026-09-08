# Capability Routing and Adversarial-Input Strategy

## Objective

The control plane should use each model or agent where it has demonstrated value without granting authority based on a product name. Routing is therefore a deterministic policy decision over declared capabilities, approved data boundaries, health, cost, task risk, and measured validation outcomes. Model consensus remains advisory; reproducible tests, policy checks, artifact digests, and human approval remain authoritative.

The worker now invokes this router before every provider call and persists the decision. The runtime still registers only deterministic local mock profiles by default; no external provider is activated.

## Decision sequence

Routing deliberately separates eligibility from optimization:

1. Determine task complexity, risk, data classification, required capabilities, and input-inspection signals.
2. Apply containment before routing if content is encoded, concealed, injection-like, an unknown binary, or suspected to be malicious.
3. Remove providers that are disabled, unhealthy, missing a capability, outside the approved execution mode or egress boundary, above the cost ceiling, or not permitted for the data classification.
4. For review, remove the producing provider. High-risk review also removes the entire producing provider family.
5. When a task requires proven performance, remove candidates below the evidence sample floor.
6. Rank only eligible providers using task-capability-specific success and validation-pass evidence. Small samples are pulled toward a neutral prior so one lucky run cannot win routing.
7. If no candidate remains, return a blocked decision with reasons. Never silently cross an egress boundary or fall back to a different provider.

Every routing result contains a policy version, the ranked eligible candidates, and explicit rejection reasons for audit and replay.

## Adaptive execution depth

| Profile | Production strategy | Review strategy | Safety posture |
|---|---|---|---|
| Simple and low risk | One producer; lean plan, implementation, and test path | One independent code reviewer | Human approval remains for privileged outcomes |
| Standard | One producer with architecture and security stages | One independent reviewer | Normal classification and egress controls |
| Complex | Two candidate producers when budget allows | Independent architecture, security, and code review | Compare candidates through deterministic validation, not model voting |
| High or critical risk | Proven providers only | Two reviewers from outside the producer family | Cross-family diversity reduces correlated failure |
| Adversarial or obfuscated | No production execution before disposition | Contained inspection, then human disposition | No external egress, network, secret resolution, or write tools |

Multiple candidates do not receive shared hidden reasoning from one another. They receive the same bounded task contract and return independently verifiable artifacts. The control plane compares test evidence and policy compliance rather than asking models to vote on which prose sounds most convincing.

## Interoperability roles

The shipped catalog describes integration boundaries, not marketing claims or fixed quality rankings:

- Claude is a model-provider candidate for bounded planning, architecture, code, test design, review, security analysis, and typed tool proposals. The control plane executes approved tools separately.
- Devin is a remote-agent candidate for longer-running repository tasks with a separate lifecycle and explicit cost/session limits.
- Windsurf Cascade is an interactive IDE handoff candidate for human-supervised implementation and review through revision-bound evidence.
- Local or future providers can implement the same profile contract and remain within the local egress boundary when their runtime genuinely does so.

All three external profiles are disabled and unhealthy by default. Activation requires an operator-approved data/egress policy, scoped credential reference, configured runtime, health check, and validation baseline. The router does not infer readiness from the presence of an adapter.

## Evidence lifecycle

Evidence is recorded per provider and capability because a system that performs well at code generation may not be the strongest security reviewer. Initial metrics include sample count, successful task rate, deterministic validation-pass rate, and p95 latency. Cost tier is an explicit constraint. Observations are already bound to the provider/model/profile version, task, attempt, workflow, and capability. Future Phase 3 telemetry should also bind evaluation-suite and repository revisions so results remain comparable and auditable.

Provider upgrades, prompt-contract changes, policy changes, and long observation gaps should trigger requalification rather than inheriting trust indefinitely. Critical routes can require a minimum evidence sample count; an unproven provider is blocked even if its first few runs look perfect.

See [Controlled continuous improvement](continuous-improvement.md) for the feedback loop, configuration, promotion boundary, and evidence-poisoning controls.

## Limits

Obfuscation detection is not proof of malicious intent, and the absence of a signal is not proof of safety. The strategy planner only establishes a conservative disposition boundary. Specialized static analysis, file-type inspection, sandboxed decoding, malware controls, and human judgment must supply the evidence needed to lift containment.

No routing system guarantees success on every project. The goal is to improve expected quality while making uncertainty, authority, evidence, and failure visible.
