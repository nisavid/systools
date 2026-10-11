import pytest

from mlxctl.domain.evidence import (
    CompatibilityAssessment,
    Evidence,
    EvidenceState,
    TrustDecision,
    TrustGrant,
)
from mlxctl.domain.resources import ModelRevision


class TestEvidence:
    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.revision = ModelRevision("mlx-community/Qwen", "b" * 40)

    def test_assessment_exposes_conflicting_evidence_and_provenance(self) -> None:
        assessment = CompatibilityAssessment(
            model_revision=self.revision,
            runtime_installation="optiq@0.2.15",
            option_fingerprint="mtp=true",
            machine_fingerprint="m3-max-128gb",
            evidence=(
                Evidence("model-card", EvidenceState.DECLARED, "MTP supported"),
                Evidence("runtime-probe", EvidenceState.CONFLICTING, "flag absent"),
            ),
        )

        assert assessment.state == EvidenceState.CONFLICTING
        assert assessment.evidence[1].source == "runtime-probe"

    def test_trust_grant_is_exact_revision_and_runtime_scoped(self) -> None:
        grant = TrustGrant(
            model_revision=self.revision,
            runtime_installation="optiq@0.2.18",
            accepted_risks=frozenset({"remote_code"}),
        )

        assert (
            grant.decide(
                revision=self.revision,
                runtime_installation="optiq@0.2.18",
                requested_risks=frozenset({"remote_code"}),
            )
            == TrustDecision.GRANTED
        )
        assert (
            grant.decide(
                revision=ModelRevision("mlx-community/Qwen", "c" * 40),
                runtime_installation="optiq@0.2.18",
                requested_risks=frozenset({"remote_code"}),
            )
            == TrustDecision.NOT_GRANTED
        )

    def test_known_security_and_integrity_failures_cannot_be_granted(self) -> None:
        grant = TrustGrant(
            model_revision=self.revision,
            runtime_installation="optiq@0.2.18",
            accepted_risks=frozenset({"remote_code", "known_security_finding"}),
        )

        assert (
            grant.decide(
                revision=self.revision,
                runtime_installation="optiq@0.2.18",
                requested_risks=frozenset({"known_security_finding"}),
            )
            == TrustDecision.FORBIDDEN
        )
        assert (
            grant.decide(
                revision=self.revision,
                runtime_installation="optiq@0.2.18",
                requested_risks=frozenset({"integrity_mismatch"}),
            )
            == TrustDecision.FORBIDDEN
        )
