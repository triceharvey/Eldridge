# Observability and Audit

## Goals

Observability must answer what is active, what failed, why a workflow is blocked, which provider and policy were used, what evidence supports a transition, and how long human gates remain open. It must do so without turning telemetry into a secret or source-code leak.

## Structured logs

Emit JSON logs to standard output in the MVP. Common fields include:

- timestamp, severity, service, environment, and version;
- workflow, task, run/attempt, agent, provider, and trace IDs;
- event/action name, state before/after, outcome, duration, and error code;
- policy version and authorization decision ID;
- tool name and sanitized argument summary;
- artifact/evidence digest, not raw content; and
- retry count, lease owner, and budget consumption.

Never log secret values, credentials, raw authorization headers, complete provider payloads, chain-of-thought, or unrestricted repository content. Redaction happens before serialization, and tests include known secret canaries.

Audit events differ from diagnostic logs. Audit events are durable, schema-versioned records committed with authoritative state changes; logs are operational signals and may be sampled or rotated.

## Metrics

Phase 3 exposes a database-derived Prometheus endpoint only when `CONTROL_PLANE_METRICS_ENABLED=true` and a separate bearer credential of at least 32 characters is configured. It does not accept the development identity header. The implemented low-cardinality set includes:

- `control_plane_workflows{state}` and `control_plane_tasks{status}`;
- `control_plane_provider_observations_total{provider,outcome}`;
- `control_plane_provider_latency_seconds_avg{provider}`;
- `control_plane_worker_leases{condition}`;
- `control_plane_approval_gates` and `control_plane_approval_wait_seconds_max`; and
- `control_plane_ci_checks_total{conclusion}`.

These values are reconstructed from durable state rather than process-local counters, so API restarts do not reset operational evidence. Token and cost metrics remain deferred until every active adapter reports them consistently enough to avoid misleading operators.

Do not label metrics with workflow IDs, task IDs, repository paths, model prompts, or error text; high-cardinality/detail belongs in traces, logs, or audit queries.

## Tracing

Instrument command intake, policy evaluation, database transition, task lease, context build, provider call, sandbox execution, evidence validation, and approval consumption with OpenTelemetry-compatible spans. Trace context must cross worker/provider callbacks without including credentials.

Full distributed tracing can wait until an external provider and separate worker are active. Preserve correlation IDs and span-ready boundaries from Phase 1.

## Health and alerts

- Liveness reports only that a process can serve.
- Readiness checks required dependencies and whether the worker can lease tasks.
- Dependency health is not folded into liveness, avoiding restart loops during a database outage.

Initial alerts should cover stuck leases, growing ready-task backlog, repeated provider authentication failures, unusual authorization denials, audit write failure, exhausted budgets, critical security findings, and approval gates beyond an agreed service objective.

An audit-write failure is fail-closed for authoritative actions. Losing optional metrics is not.

## Dashboard and retention

The authenticated `/dashboard` renders aggregate workflow states, task status, provider success and validation rates, latency, worker leases, human gates, and CI conclusions. It contains no workflow IDs, repository paths, prompts, titles, or raw error text and sends restrictive CSP, framing, referrer, caching, and content-type headers. `/operations/summary` provides the same authorized data as JSON. Grafana remains optional now that `/metrics` exists.

Retention must follow data classification. Audit metadata normally outlives diagnostic logs. Raw provider payload retention is disabled by default; when troubleshooting requires payload capture, use an explicit time-bound, access-controlled mode with redaction and an audit event.

## Operational questions and evidence

| Question | Source |
|---|---|
| Why did this workflow stop? | Current state, blocking finding, task attempts, transition audit events |
| Who authorized merge/deploy? | Scoped approval plus identity and use event |
| Did tests apply to the approved revision? | Revision digest on test evidence, review, and approval |
| Is a provider unhealthy? | Provider health, latency/error metrics, normalized error logs |
| Did a worker die mid-task? | Lease/heartbeat history and sandbox reconciliation event |
| What data left the trust boundary? | Context manifest classification, hashes, provider destination, policy decision |
