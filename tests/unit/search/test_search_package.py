"""Package-level contract for the MVP search vertical slice."""

from omf_retrieval.application import search


def test_search_package_exposes_hybrid_search_behavior() -> None:
    """The package must expose behavior, not remain an empty scaffold."""
    assert callable(getattr(search, "reciprocal_rank_fusion", None))
