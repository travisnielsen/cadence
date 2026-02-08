"""Smoke test to verify test infrastructure works."""


def test_imports() -> None:
    """Verify that src modules can be imported."""
    from models import schema  # noqa: F401

    assert True
