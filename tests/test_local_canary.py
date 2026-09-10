from __future__ import annotations

from control_plane.domain import ProviderResult
from control_plane.local_canary import canary_fixtures, run_canary


class FixtureProvider:
    name = "local-openai-compatible"

    def __init__(self, *, healthy: bool = True, alter_last: bool = False) -> None:
        self.healthy = healthy
        self.alter_last = alter_last
        self.submitted = 0

    def health(self) -> bool:
        return self.healthy

    def submit(self, _request: object) -> ProviderResult:
        fixture = canary_fixtures()[self.submitted]
        self.submitted += 1
        if fixture.name == "structured_plan":
            output = {
                "summary": "contained local plan",
                "steps": ["inspect", "validate", "report"],
                "requires_human_approval": False,
            }
        elif fixture.name == "untrusted_instruction_containment":
            output = {
                "followed_untrusted_instruction": False,
                "claimed_human_approval": False,
                "action": "blocked",
            }
        else:
            output = {
                "project": "Project Atlas",
                "approved_facts": ["blue emblem", "offline first"],
                "invented_facts": [],
            }
            if self.alter_last:
                output = {**output, "invented_facts": ["unapproved"]}
        return ProviderResult(
            status="SUCCEEDED",
            output=output,
            provider=self.name,
            model="test-model",
            usage={"input_tokens": 10, "output_tokens": 5},
        )


def test_canary_passes_only_when_health_and_every_fixture_pass() -> None:
    report = run_canary(
        FixtureProvider(),
        model="test-model",
        endpoint="http://127.0.0.1:11434",
        runtime="test",
        artifact_digest="sha256:" + "a" * 64,
    )

    assert report["passed"] is True
    assert len(report["fixtures"]) == 3
    assert all(item["passed"] for item in report["fixtures"])
    assert report["authority"] == {
        "tools": False,
        "merge": False,
        "deploy": False,
        "human_approval": False,
    }


def test_canary_fails_closed_on_unhealthy_runtime() -> None:
    report = run_canary(
        FixtureProvider(healthy=False),
        model="test-model",
        endpoint="http://127.0.0.1:11434",
        runtime="test",
        artifact_digest="sha256:" + "a" * 64,
    )

    assert report["passed"] is False
    assert report["fixtures"] == []


def test_canary_fails_when_context_fidelity_changes() -> None:
    provider = FixtureProvider(alter_last=True)
    report = run_canary(
        provider,
        model="test-model",
        endpoint="http://127.0.0.1:11434",
        runtime="test",
        artifact_digest="sha256:" + "a" * 64,
    )

    assert report["passed"] is False
    assert report["fixtures"][-1]["name"] == "source_context_fidelity"
    assert report["fixtures"][-1]["passed"] is False
