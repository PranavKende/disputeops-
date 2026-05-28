"""RecoveryOracle — recovery probability scoring agent.

Public interface:
    score_recovery(verdict, case) -> RecoveryScore
    ScoringContext                              — deps dataclass for testing
"""

from agents.recovery_oracle.recovery_oracle import ScoringContext, score_recovery

__all__ = ["score_recovery", "ScoringContext"]
