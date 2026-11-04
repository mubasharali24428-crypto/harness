"""A tiny module with a real bug: `increment` skips a number."""


def increment(numbers: list[int]) -> list[int]:
    """Return each number plus one."""
    return [number for number in numbers]  # BUG: forgot the + 1
