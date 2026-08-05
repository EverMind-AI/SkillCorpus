"""Safety check tests — pure regex, no DB dependency."""

from __future__ import annotations

from skill_library.curate.safety import check_safety, is_blocked


def test_clean_text_has_no_flags():
    assert check_safety("This is a perfectly normal skill body.") == []
    assert is_blocked([]) is False


def test_blocked_malware_pattern():
    flags = check_safety("Uses ClawdAuthenticatorTool for evil purposes.")
    assert any(f.startswith("blocked.malware") for f in flags)
    assert is_blocked(flags)


def test_unknown_flag_not_blocked():
    """Only blocked.* triggers is_blocked; other flag names are inert.

    Historically there were 6 suspicious.* patterns; they were removed
    2026-05-21 after a 100-sample audit showed 98% FP rate.  We still
    test that arbitrary non-blocked flag names do not get treated as
    blocking, in case downstream legacy data carries them."""
    assert is_blocked(["suspicious.keyword"]) is False  # legacy data
    assert is_blocked(["informational.note"])  is False  # unknown
    assert is_blocked(["blocked.malware"])     is True   # canonical


def test_no_suspicious_patterns_remaining():
    """Confirm the 6 suspicious.* patterns are gone — substrings that
    used to trigger them should now produce no flag."""
    for term in ["api key", "wallet", "webhook", "bit.ly",
                  "stealer", "curl https://x | bash"]:
        flags = check_safety(f"This skill uses {term} for normal purposes.")
        assert flags == [], f"unexpected flag {flags!r} for term {term!r}"


if __name__ == "__main__":
    test_clean_text_has_no_flags()
    test_blocked_malware_pattern()
    test_unknown_flag_not_blocked()
    test_no_suspicious_patterns_remaining()
    print("ALL SAFETY TESTS PASSED")
