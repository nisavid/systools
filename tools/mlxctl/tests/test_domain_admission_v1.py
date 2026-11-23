import pytest

from mlxctl.domain.admission import (
    AdmissionDecision,
    FitAssessment,
    FitClass,
    PressureAction,
    PressureLevel,
    PressurePolicy,
    RunningService,
)


class TestAdmissionPolicy:
    def test_likely_fit_starts_and_borderline_requires_confirmation(self) -> None:
        assert (
            FitAssessment(FitClass.LIKELY, 40, 64, ("measured weights",)).decision
            == AdmissionDecision.START
        )
        assert (
            FitAssessment(FitClass.BORDERLINE, 58, 64, ("derived KV",)).decision
            == AdmissionDecision.CONFIRM
        )
        assert (
            FitAssessment(FitClass.UNKNOWN, None, 64, ("missing config",)).decision
            == AdmissionDecision.CONFIRM
        )

    def test_no_fit_requires_named_transition_plan(self) -> None:
        fit = FitAssessment(FitClass.NO_FIT, 80, 64, ("exact model bytes",))
        assert fit.decision == AdmissionDecision.TRANSITION_PLAN
        with pytest.raises(ValueError, match="named transition"):
            fit.approve_transition(())
        assert fit.approve_transition(("stop:chat",)) == ("stop:chat",)

    def test_critical_pressure_sheds_then_stops_lru_idle_unpinned(self) -> None:
        services = (
            RunningService("coding", pinned=True, busy=False, last_used_ns=1),
            RunningService("chat", pinned=False, busy=False, last_used_ns=2),
            RunningService("vision", pinned=False, busy=True, last_used_ns=0),
        )

        result = PressurePolicy().evaluate(PressureLevel.CRITICAL, services)

        assert result.actions[0] == PressureAction.SHED_NEW_WORK
        assert result.stop_services == ("chat",)
        assert "coding" not in result.stop_services

    def test_only_pinned_or_busy_services_produces_explicit_stop_plan(self) -> None:
        services = (
            RunningService("coding", pinned=True, busy=False, last_used_ns=1),
            RunningService("vision", pinned=False, busy=True, last_used_ns=2),
        )

        result = PressurePolicy().evaluate(PressureLevel.CRITICAL, services)

        assert result.stop_services == ()
        assert PressureAction.PRESENT_STOP_PLAN in result.actions
        assert result.operator_stop_plan == ("vision", "coding")
