from control_plane.routing import (
    CapabilityEvidence,
    CapabilityRouter,
    CostTier,
    DataClassification,
    EgressBoundary,
    ExecutionMode,
    ProviderProfile,
    RiskLevel,
    RoutingObjective,
    RoutingPurpose,
    RoutingRequest,
    WorkCapability,
    interoperability_profiles,
)


def profile(
    provider_id: str,
    *,
    family: str = "family-a",
    capability: WorkCapability = WorkCapability.CODE_GENERATION,
    enabled: bool = True,
    healthy: bool = True,
    boundary: EgressBoundary = EgressBoundary.LOCAL,
    maximum_data: DataClassification = DataClassification.RESTRICTED,
    cost: CostTier = CostTier.MEDIUM,
    maximum_risk: RiskLevel = RiskLevel.CRITICAL,
    evidence: CapabilityEvidence | None = None,
) -> ProviderProfile:
    return ProviderProfile(
        provider_id=provider_id,
        provider_family=family,
        execution_mode=ExecutionMode.MODEL,
        capabilities=frozenset({capability}),
        egress_boundary=boundary,
        maximum_data_classification=maximum_data,
        cost_tier=cost,
        maximum_risk=maximum_risk,
        enabled=enabled,
        healthy=healthy,
        evidence={capability: evidence} if evidence else {},
    )


def request(**overrides: object) -> RoutingRequest:
    values: dict[str, object] = {
        "required_capabilities": frozenset({WorkCapability.CODE_GENERATION}),
        "data_classification": DataClassification.INTERNAL,
        "risk": RiskLevel.MEDIUM,
    }
    values.update(overrides)
    return RoutingRequest(**values)  # type: ignore[arg-type]


def test_router_selects_best_eligible_provider_from_measured_evidence() -> None:
    weak = CapabilityEvidence(30, 0.6, 0.7, 20)
    strong = CapabilityEvidence(30, 0.95, 0.9, 25)

    decision = CapabilityRouter().route(
        request(),
        (profile("weak", evidence=weak), profile("strong", evidence=strong)),
    )

    assert decision.selected_provider_id == "strong"
    assert tuple(candidate.provider_id for candidate in decision.ranked_candidates) == (
        "strong",
        "weak",
    )


def test_policy_filters_run_before_performance_scoring() -> None:
    excellent_but_external = profile(
        "external",
        boundary=EgressBoundary.APPROVED_EXTERNAL,
        maximum_data=DataClassification.INTERNAL,
        evidence=CapabilityEvidence(100, 1.0, 1.0),
    )
    local = profile("local", evidence=CapabilityEvidence(20, 0.7, 0.7))

    decision = CapabilityRouter().route(request(), (excellent_but_external, local))

    assert decision.selected_provider_id == "local"
    assert "egress_boundary_not_approved" in decision.rejected["external"]


def test_router_rejects_work_above_provider_risk_ceiling() -> None:
    decision = CapabilityRouter().route(
        request(risk=RiskLevel.HIGH),
        (profile("medium-only", maximum_risk=RiskLevel.MEDIUM),),
    )

    assert decision.blocked
    assert "risk_level_not_allowed" in decision.rejected["medium-only"]


def test_router_rejects_restricted_data_disabled_unhealthy_and_missing_capability() -> None:
    profiles = (
        profile(
            "external",
            boundary=EgressBoundary.APPROVED_EXTERNAL,
            maximum_data=DataClassification.INTERNAL,
        ),
        profile("disabled", enabled=False),
        profile("unhealthy", healthy=False),
        profile("wrong-capability", capability=WorkCapability.PLANNING),
    )
    decision = CapabilityRouter().route(
        request(
            data_classification=DataClassification.RESTRICTED,
            allowed_egress=frozenset({EgressBoundary.LOCAL, EgressBoundary.APPROVED_EXTERNAL}),
        ),
        profiles,
    )

    assert decision.blocked
    assert "data_classification_not_allowed" in decision.rejected["external"]
    assert "provider_disabled" in decision.rejected["disabled"]
    assert "provider_unhealthy" in decision.rejected["unhealthy"]
    assert decision.rejected["wrong-capability"][0].startswith("missing_capabilities:")


def test_high_risk_review_requires_a_different_provider_family() -> None:
    decision = CapabilityRouter().route(
        request(
            required_capabilities=frozenset({WorkCapability.CODE_REVIEW}),
            purpose=RoutingPurpose.REVIEW,
            risk=RiskLevel.HIGH,
            producer_provider_id="producer",
            producer_family="family-a",
        ),
        (
            profile("producer", capability=WorkCapability.CODE_REVIEW),
            profile("sibling", capability=WorkCapability.CODE_REVIEW),
            profile("independent", family="family-b", capability=WorkCapability.CODE_REVIEW),
        ),
    )

    assert decision.selected_provider_id == "independent"
    assert "reviewer_must_differ_from_producer" in decision.rejected["producer"]
    assert "high_risk_review_requires_provider_family_diversity" in decision.rejected["sibling"]


def test_small_samples_are_shrunk_toward_neutral_quality() -> None:
    one_lucky_run = CapabilityEvidence(1, 1.0, 1.0)
    established = CapabilityEvidence(20, 0.8, 0.8)

    decision = CapabilityRouter().route(
        request(),
        (
            profile("lucky", evidence=one_lucky_run),
            profile("established", evidence=established),
        ),
    )

    assert decision.selected_provider_id == "established"


def test_operator_objective_changes_ranking_without_changing_eligibility() -> None:
    fast_expensive = profile(
        "fast-expensive",
        cost=CostTier.HIGH,
        evidence=CapabilityEvidence(30, 0.9, 0.9, 2),
    )
    slow_frugal = profile(
        "slow-frugal",
        cost=CostTier.LOW,
        evidence=CapabilityEvidence(30, 0.82, 0.82, 120),
    )

    speed = CapabilityRouter().route(
        request(objective=RoutingObjective.SPEED), (slow_frugal, fast_expensive)
    )
    frugal = CapabilityRouter().route(
        request(objective=RoutingObjective.FRUGAL), (slow_frugal, fast_expensive)
    )

    assert speed.selected_provider_id == "fast-expensive"
    assert frugal.selected_provider_id == "slow-frugal"
    assert speed.objective_profile_version == "routing-objectives/v1"
    assert speed.ranked_candidates[0].latency_utility > speed.ranked_candidates[1].latency_utility


def test_objective_cannot_rescue_a_policy_ineligible_provider() -> None:
    external = profile(
        "external",
        boundary=EgressBoundary.APPROVED_EXTERNAL,
        maximum_data=DataClassification.INTERNAL,
        cost=CostTier.LOW,
        evidence=CapabilityEvidence(100, 1.0, 1.0, 0.1),
    )

    decision = CapabilityRouter().route(request(objective=RoutingObjective.QUALITY), (external,))

    assert decision.blocked
    assert decision.ranked_candidates == ()
    assert decision.rejected["external"] == ("egress_boundary_not_approved",)


def test_ranked_candidates_expose_interpretable_score_components() -> None:
    decision = CapabilityRouter().route(
        request(objective=RoutingObjective.BALANCED),
        (profile("candidate", cost=CostTier.LOW, evidence=CapabilityEvidence(20, 0.8, 0.6, 30)),),
    )

    candidate = decision.ranked_candidates[0]
    assert candidate.quality_utility == 0.7
    assert candidate.cost_utility == 1.0
    assert candidate.latency_utility == 0.35
    assert candidate.score == 0.71


def test_evidence_floor_blocks_unproven_provider_for_sensitive_route() -> None:
    decision = CapabilityRouter().route(
        request(minimum_evidence_samples=20),
        (profile("unproven", evidence=CapabilityEvidence(3, 1.0, 1.0)),),
    )

    assert decision.blocked
    assert decision.rejected["unproven"] == ("insufficient_evidence:CODE_GENERATION",)


def test_duplicate_provider_ids_are_rejected_as_ambiguous_configuration() -> None:
    duplicate = profile("duplicate")

    try:
        CapabilityRouter().route(request(), (duplicate, duplicate))
    except ValueError as exc:
        assert str(exc) == "provider_id values must be unique"
    else:
        raise AssertionError("ambiguous provider configuration was accepted")


def test_interoperability_profiles_are_descriptive_and_disabled_by_default() -> None:
    profiles = interoperability_profiles()

    assert {item.provider_id for item in profiles} == {
        "anthropic-claude",
        "devin",
        "windsurf-cascade",
    }
    assert not any(item.enabled or item.healthy for item in profiles)
