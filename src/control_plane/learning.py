from __future__ import annotations

from dataclasses import replace
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane.persistence import ProviderObservation
from control_plane.routing import CapabilityEvidence, ProviderProfile, WorkCapability


class ProviderEvidenceStore:
    """Build bounded routing evidence from validation-backed observations."""

    def __init__(self, *, window_size: int = 100) -> None:
        if window_size < 1:
            raise ValueError("window_size must be positive")
        self.window_size = window_size

    def hydrate_profiles(
        self, session: Session, profiles: tuple[ProviderProfile, ...]
    ) -> tuple[ProviderProfile, ...]:
        return tuple(self._hydrate_profile(session, profile) for profile in profiles)

    def _hydrate_profile(self, session: Session, profile: ProviderProfile) -> ProviderProfile:
        evidence = {
            capability: self._evidence_for(session, profile, capability)
            for capability in profile.capabilities
        }
        return replace(profile, evidence=evidence)

    def _evidence_for(
        self,
        session: Session,
        profile: ProviderProfile,
        capability: WorkCapability,
    ) -> CapabilityEvidence:
        observations = session.scalars(
            select(ProviderObservation)
            .where(
                ProviderObservation.provider_id == profile.provider_id,
                ProviderObservation.model_version == profile.model_version,
                ProviderObservation.profile_version == profile.profile_version,
                ProviderObservation.work_capability == capability.value,
            )
            .order_by(ProviderObservation.created_at.desc())
            .limit(self.window_size)
        ).all()
        count = len(observations)
        if count == 0:
            return CapabilityEvidence()
        latencies = sorted(item.latency_ms / 1000 for item in observations)
        p95_index = max(ceil(0.95 * count) - 1, 0)
        return CapabilityEvidence(
            sample_count=count,
            success_rate=sum(item.succeeded for item in observations) / count,
            validation_pass_rate=sum(item.validation_passed for item in observations) / count,
            p95_latency_seconds=latencies[p95_index],
        )
