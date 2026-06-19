"""Shared A/B test-suite library (framework-agnostic).

All metric/ground-truth logic lives here so it has ONE implementation, reused by both the
standalone runner (tools/ab_report.py) and the pytest wrappers (tests/test_*.py).
"""
