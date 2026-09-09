from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
K3D_DIR = ROOT / "deployment" / "k3d"


def test_k3d_boundary_is_pinned_and_least_privilege() -> None:
    cluster = (K3D_DIR / "phase4-validation.yaml").read_text()
    identity = (K3D_DIR / "identity-boundary.yaml").read_text()
    validator = (K3D_DIR / "validate-boundary.sh").read_text()
    broker_validator = (K3D_DIR / "validate-broker.py").read_text()
    deployment_validator = (K3D_DIR / "validate-deployment.py").read_text()

    assert "rancher/k3s@sha256:2074403abe1bded11ef3dde09d457e13" in cluster
    assert "disableLoadbalancer: true" in cluster
    assert "updateDefaultKubeconfig: false" in cluster
    assert "automountServiceAccountToken: false" in identity
    assert "resources:\n      - pods" in identity
    assert "resources:\n      - deployments" in identity
    assert "secrets" not in identity
    assert identity.count("verbs:\n      - get\n      - list\n      - watch") == 2
    assert "KubernetesTokenRequestBroker" in broker_validator
    assert 'audience="eldridge-local-k3d"' in broker_validator
    assert "lifetime_seconds=600" in broker_validator
    assert "enabled=True" in broker_validator
    assert "LocalK3dConfigMapAdapter" in deployment_validator
    assert '"first_changed"' in deployment_validator
    assert '"replay_changed"' in deployment_validator
    assert "PYTHONPATH=" in validator
    assert 'rm -r "${VALIDATION_TMP_DIR}"' in validator


@pytest.mark.k3d
@pytest.mark.skipif(
    os.environ.get("CONTROL_PLANE_RUN_K3D_TEST") != "1",
    reason="set CONTROL_PLANE_RUN_K3D_TEST=1 for the destructive-local-state k3d exercise",
)
def test_live_ephemeral_k3d_identity_boundary() -> None:
    for tool in ("docker", "k3d", "kubectl", "tofu"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is not installed")

    result = subprocess.run(  # noqa: S603 - fixed repository-owned script path
        [str(K3D_DIR / "validate-boundary.sh")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert "result=passed cleanup=armed" in result.stdout
    assert '"aud": "eldridge-local-k3d"' in result.stdout
    assert '"broker_id": "kubernetes-token-request-v1"' in result.stdout
    assert '"operation": "OBSERVE"' in result.stdout
    assert '"reference_scheme": "kubernetes-token"' in result.stdout
    assert '"lifetime_seconds": 600' in result.stdout
    assert '"first_changed": true' in result.stdout
    assert '"first_verified": true' in result.stdout
    assert '"replay_changed": false' in result.stdout
    assert '"replay_verified": true' in result.stdout
    assert result.stdout.count("rbac expected=yes actual=yes") == 4
    assert result.stdout.count("rbac expected=no actual=no") == 8
