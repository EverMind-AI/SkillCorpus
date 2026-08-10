"""Safety check tests — pure regex, no DB dependency."""

from __future__ import annotations

import re

from skillcorpus.curate import safety
from skillcorpus.curate.safety import check_safety, is_blocked


def test_clean_text_has_no_flags():
    assert check_safety("This is a perfectly normal skill body.") == []
    assert is_blocked([]) is False


def test_blocked_malware_pattern():
    """The single blocked.malware regex rejects the known-malicious signature; a
    benign lookalike token is not matched."""
    flags = check_safety("This skill wraps ClawdAuthenticatorTool to steal creds.")
    assert flags == ["blocked.malware"]
    assert is_blocked(flags)
    assert check_safety("Uses a generic AuthenticatorTool for normal login.") == []


def test_operator_rule_blocks(monkeypatch):
    """Operators may register their own ``blocked.*`` patterns; the convention
    forces rejection at ingest."""
    monkeypatch.setattr(
        safety, "_SAFETY_RULES",
        [("blocked.custom", re.compile(r"FORBIDDEN_TOKEN"))],
    )
    flags = safety.check_safety("this body contains a FORBIDDEN_TOKEN marker")
    assert flags == ["blocked.custom"]
    assert safety.is_blocked(flags)


def test_unknown_flag_not_blocked():
    """Only blocked.* triggers is_blocked; other flag names are inert.

    The former suspicious.* substring patterns are not gates (a stratified
    audit measured their false-positive rate above 90%). We still test that
    arbitrary non-blocked flag names are not treated as blocking, in case
    downstream legacy data carries them."""
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
