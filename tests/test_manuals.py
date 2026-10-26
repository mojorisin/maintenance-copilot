"""Tests for manual parsing and BM25 retrieval.

RETRIEVAL_CASES is a small retrieval eval: each realistic query must return
the expected section first. KNOWN_GAPS documents where keyword search fails
(synonyms); they are marked xfail so an upgrade to semantic or hybrid search
shows up as unexpected passes.
"""

import pytest

from maintenance_copilot.manuals import ManualIndex, tokenize

PUMP = "manual:centrifugal-pump-cp"
COMP = "manual:reciprocating-compressor-rc"
GT = "manual:gas-turbine-ad30"

RETRIEVAL_CASES = [
    (
        "What does the manual say about a high-vibration alarm?",
        "gas_turbine",
        f"{GT}#high-vibration-alarm",
    ),
    ("high vibration alarm", "pump", f"{PUMP}#high-vibration-alarm"),
    ("high vibration alarm", "compressor", f"{COMP}#high-vibration-alarm"),
    ("pump seal keeps failing, silt in the river water", None, f"{PUMP}#mechanical-seal-leakage"),
    ("repeated compressor valve failures", None, f"{COMP}#compressor-valve-failure"),
    ("T30 and T50 rising, Ps30 rising", None, f"{GT}#hpc-degradation"),
    ("offline water wash did not recover performance", None, f"{GT}#compressor-water-wash"),
    (
        "RUL status critical, what maintenance to plan",
        "gas_turbine",
        f"{GT}#remaining-life-based-maintenance-planning",
    ),
    ("bearing failed again after replacement", "pump", f"{PUMP}#bearing-failure"),
    ("discharge temperature high", "compressor", f"{COMP}#high-discharge-temperature"),
    ("pump making a gravel-like noise", "pump", f"{PUMP}#cavitation"),
    ("EGT spread between exhaust thermocouples", None, f"{GT}#egt-spread-and-fuel-nozzles"),
]

KNOWN_GAPS = [
    ("the pump is shaking a lot", "pump", f"{PUMP}#high-vibration-alarm"),
    ("turbine running hot, losing efficiency", "gas_turbine", f"{GT}#hpc-degradation"),
]


@pytest.fixture(scope="module")
def index():
    return ManualIndex.load()


@pytest.mark.parametrize(("query", "asset_type", "expected"), RETRIEVAL_CASES)
def test_retrieval_top1(index, query, asset_type, expected):
    assert index.search(query, asset_type)[0].section.id == expected


@pytest.mark.xfail(reason="lexical gap: BM25 has no notion of synonyms", strict=True)
@pytest.mark.parametrize(("query", "asset_type", "expected"), KNOWN_GAPS)
def test_known_synonym_gaps(index, query, asset_type, expected):
    assert index.search(query, asset_type)[0].section.id == expected


def test_every_manual_parses_with_unique_ids(index):
    ids = [s.id for s in index.sections]
    assert len(ids) == len(set(ids))
    assert {s.asset_type for s in index.sections} == {"pump", "compressor", "gas_turbine"}


def test_section_lookup_by_citation_id(index):
    section = index.get(f"{GT}#high-vibration-alarm")
    assert section is not None
    assert "25.4 mm/s" in section.text
    assert index.get("manual:nope#nothing") is None


def test_asset_type_filter_is_strict(index):
    hits = index.search("vibration", asset_type="compressor", k=10)
    assert hits
    assert all(h.section.asset_type == "compressor" for h in hits)


def test_unrelated_query_returns_nothing(index):
    assert index.search("what is the capital of France") == []


def test_tokenize_normalises_plurals_and_stopwords():
    assert tokenize("The Seals are failing") == ["seal", "failing"]
    assert tokenize("T30 rising") == ["t30", "rising"]
