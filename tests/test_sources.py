import pytest

from src.sources import SourceError, check_no_raw_text, registry, render_sources_md, require_ingestable, SOURCES_MD


def test_allowed_sources_ingestable():
    assert require_ingestable("S0-synthetic-seed").status == "allowed"


@pytest.mark.parametrize("sid", ["C1-manufacturer-spec-pages", "C2-retailer-official-api", "C3-gs1-registry",
                                 "C4-open-food-facts", "X1-customer-reviews-qa", "not-registered"])
def test_blocked_reference_prohibited_and_unknown_rejected(sid):
    with pytest.raises(SourceError):
        require_ingestable(sid)


def test_reviews_never_evidence():
    reviews = [s for s in registry().values() if s.kind == "user_generated"]
    assert reviews and all(s.status == "prohibited" for s in reviews)


def test_raw_text_rejected():
    check_no_raw_text({"attributes": {"weight": {"value": 1, "unit": "g"}}, "source_url": "x" * 500})
    with pytest.raises(SourceError):
        check_no_raw_text({"page_text": "..."})
    with pytest.raises(SourceError):
        check_no_raw_text({"attributes": {"note": {"value": "x" * 1000}}})


def test_sources_md_in_sync_with_registry():
    assert SOURCES_MD.exists(), "run `python -m src.cli sources`"
    assert SOURCES_MD.read_text(encoding="utf-8") == render_sources_md()
    for sid in registry():
        assert f"`{sid}`" in render_sources_md()
