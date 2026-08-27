"""Pure calibration-margin assessment for the fixed six-query smoke set."""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class KnownEvidenceScores:
    """Best expected-evidence score observed in each retrieval lane."""

    case_id: str
    keyword_score: float | None
    vector_score: float | None

    def __post_init__(self) -> None:
        if type(self.case_id) is not str or not self.case_id.strip():
            raise ValueError("Invalid calibration case ID")
        for score in (self.keyword_score, self.vector_score):
            if score is not None and (
                type(score) is not float
                or not isfinite(score)
                or not 0.0 <= score <= 1.0
            ):
                raise ValueError("Invalid calibration score")


@dataclass(frozen=True, slots=True)
class CalibrationAssessment:
    """State whether one strict floor pair can separate known from unknown."""

    calibratable: bool
    margin: float | None
    unknown_keyword_max: float
    unknown_vector_max: float


def assess_calibration(
    known: tuple[KnownEvidenceScores, ...],
    *,
    unknown_keyword_max: float,
    unknown_vector_max: float,
) -> CalibrationAssessment:
    """Require every known case to beat an unknown maximum in at least one lane."""
    if not known:
        raise ValueError("Known calibration cases are required")
    for score in (unknown_keyword_max, unknown_vector_max):
        if type(score) is not float or not isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("Invalid unknown calibration maximum")

    case_margins: list[float] = []
    for score in known:
        if type(score) is not KnownEvidenceScores:
            raise ValueError("Invalid known calibration case")
        lane_margins = []
        if score.keyword_score is not None:
            lane_margins.append(score.keyword_score - unknown_keyword_max)
        if score.vector_score is not None:
            lane_margins.append(score.vector_score - unknown_vector_max)
        if not lane_margins:
            return CalibrationAssessment(
                False,
                None,
                unknown_keyword_max,
                unknown_vector_max,
            )
        case_margins.append(max(lane_margins))

    margin = min(case_margins)
    return CalibrationAssessment(
        margin > 0.0,
        margin,
        unknown_keyword_max,
        unknown_vector_max,
    )


__all__ = [
    "CalibrationAssessment",
    "KnownEvidenceScores",
    "assess_calibration",
]
