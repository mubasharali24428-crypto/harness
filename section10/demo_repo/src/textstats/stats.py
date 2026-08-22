"""Pure functions over plain text. Standard library only, by design."""


def word_count(text: str) -> int:
    """Number of whitespace-separated words. Empty/blank input counts as 0."""
    return len(text.split())


def top_words(text: str, n: int = 5) -> list[tuple[str, int]]:
    """The n most frequent words, case-insensitive, ties broken alphabetically."""
    counts: dict[str, int] = {}
    for word in text.lower().split():
        counts[word] = counts.get(word, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ordered[:n]


def median_word_length(text: str) -> float:
    """Median length of whitespace-separated words; 0 for empty input.

    TODO(demo-task): implement me. The failing test is
    tests/test_stats.py::test_median_word_length.
    """
    raise NotImplementedError("demo task: implement median_word_length")
