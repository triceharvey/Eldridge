from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily


@dataclass(eq=False, frozen=True)
class SnapshotCollector:
    snapshot: dict[str, Any]

    def collect(self) -> list[GaugeMetricFamily]:
        metrics: list[GaugeMetricFamily] = []

        workflows = GaugeMetricFamily(
            "control_plane_workflows", "Current workflows by state", labels=["state"]
        )
        for state, count in sorted(self.snapshot["workflows_by_state"].items()):
            workflows.add_metric([state], count)
        metrics.append(workflows)

        tasks = GaugeMetricFamily(
            "control_plane_tasks", "Current tasks by status", labels=["status"]
        )
        for task_status, count in sorted(self.snapshot["tasks_by_status"].items()):
            tasks.add_metric([task_status], count)
        metrics.append(tasks)

        provider_requests = GaugeMetricFamily(
            "control_plane_provider_observations_total",
            "Durable provider observations by provider and outcome",
            labels=["provider", "outcome"],
        )
        provider_latency = GaugeMetricFamily(
            "control_plane_provider_latency_seconds_avg",
            "Average observed provider latency",
            labels=["provider"],
        )
        for provider in self.snapshot["providers"]:
            provider_requests.add_metric(
                [provider["provider_id"], "succeeded"], provider["succeeded"]
            )
            provider_requests.add_metric([provider["provider_id"], "failed"], provider["failed"])
            provider_latency.add_metric(
                [provider["provider_id"]], provider["average_latency_ms"] / 1000
            )
        metrics.extend((provider_requests, provider_latency))

        leases = GaugeMetricFamily(
            "control_plane_worker_leases", "Current worker leases", labels=["condition"]
        )
        leases.add_metric(["active"], self.snapshot["leases"]["active"])
        leases.add_metric(["expired"], self.snapshot["leases"]["expired"])
        metrics.append(leases)

        approvals = GaugeMetricFamily(
            "control_plane_approval_gates", "Current workflows waiting for human approval"
        )
        approvals.add_metric([], self.snapshot["approval_gates"]["waiting"])
        metrics.append(approvals)
        approval_wait = GaugeMetricFamily(
            "control_plane_approval_wait_seconds_max",
            "Age of the oldest workflow waiting for human approval",
        )
        approval_wait.add_metric([], self.snapshot["approval_gates"]["oldest_wait_seconds"])
        metrics.append(approval_wait)

        ci = GaugeMetricFamily(
            "control_plane_ci_checks_total",
            "Durable CI check evidence by conclusion",
            labels=["conclusion"],
        )
        for conclusion, count in sorted(self.snapshot["ci_checks_by_conclusion"].items()):
            ci.add_metric([conclusion], count)
        metrics.append(ci)
        return metrics


def render_prometheus(snapshot: dict[str, Any]) -> bytes:
    registry = CollectorRegistry(auto_describe=False)
    registry.register(SnapshotCollector(snapshot))
    return generate_latest(registry)


def render_dashboard(snapshot: dict[str, Any]) -> str:
    def cards(values: dict[str, int]) -> str:
        return (
            "".join(
                f'<div class="card"><strong>{escape(name)}</strong><span>{count}</span></div>'
                for name, count in sorted(values.items())
            )
            or '<div class="empty">No evidence recorded</div>'
        )

    provider_rows = (
        "".join(
            "<tr>"
            f"<td>{escape(item['provider_id'])}</td><td>{item['observations']}</td>"
            f"<td>{item['success_rate']:.1%}</td><td>{item['validation_rate']:.1%}</td>"
            f"<td>{item['average_latency_ms']:.0f} ms</td>"
            "</tr>"
            for item in snapshot["providers"]
        )
        or '<tr><td colspan="5">No provider observations recorded</td></tr>'
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta http-equiv="refresh" content="30"><title>AI Control Plane Operations</title>
<style>
:root{{--bg:#08111f;--panel:#111d2f;--line:#263750;--text:#e8eef7;--muted:#9cb0c9;
--good:#55d6a9}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font:15px system-ui}}
main{{max-width:1180px;margin:auto;padding:32px}}
h1{{margin:0 0 6px;font-size:28px}}h2{{margin-top:32px}}
.sub,.empty{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:16px;display:flex;flex-direction:column;gap:10px}}
.card span{{font-size:28px;color:var(--good)}}
table{{width:100%;border-collapse:collapse;background:var(--panel)}}
th,td{{padding:12px;text-align:left;border-bottom:1px solid var(--line)}}
th{{color:var(--muted)}}
.summary{{display:flex;gap:20px;flex-wrap:wrap;margin:18px 0}}
.pill{{padding:10px 14px;background:var(--panel);border-radius:20px}}
</style></head><body><main><h1>AI Engineering Control Plane</h1>
<div class="sub">Operational evidence snapshot · refreshed every 30 seconds ·
generated {escape(snapshot["generated_at"])}</div>
<div class="summary"><div class="pill">Active leases: {snapshot["leases"]["active"]}</div>
<div class="pill">Expired leases: {snapshot["leases"]["expired"]}</div>
<div class="pill">Human gates: {snapshot["approval_gates"]["waiting"]}</div>
<div class="pill">Oldest gate: {snapshot["approval_gates"]["oldest_wait_seconds"]:.0f}s</div></div>
<h2>Workflow states</h2><div class="grid">{cards(snapshot["workflows_by_state"])}</div>
<h2>Task status</h2><div class="grid">{cards(snapshot["tasks_by_status"])}</div>
<h2>Provider evidence</h2><table><thead><tr><th>Provider</th><th>Runs</th><th>Success</th>
<th>Validation</th><th>Average latency</th></tr></thead><tbody>{provider_rows}</tbody></table>
<h2>CI conclusions</h2><div class="grid">{cards(snapshot["ci_checks_by_conclusion"])}</div>
</main></body></html>"""
