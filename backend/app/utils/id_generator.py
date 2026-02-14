"""Utility for generating unique test IDs."""

import uuid


def generate_test_id(prefix: str = "TC") -> str:
    """Generate a unique test case ID like TC-A1B2C3D4."""
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def generate_suite_id(prefix: str = "TS") -> str:
    """Generate a unique test suite ID like TS-E5F6G7H8."""
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"
