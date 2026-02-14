"""Custom validators for request data."""


def validate_user_story(story: str) -> str:
    """Basic story format validation."""
    story = story.strip()
    if len(story) < 10:
        raise ValueError("User story must be at least 10 characters long")
    return story


def validate_acceptance_criteria(criteria: list[str]) -> list[str]:
    """Filter empty criteria and strip whitespace."""
    return [c.strip() for c in criteria if c.strip()]
