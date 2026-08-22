from textstats import median_word_length, top_words, word_count


def test_word_count():
    assert word_count("") == 0
    assert word_count("   ") == 0
    assert word_count("one two three") == 3
    assert word_count("  spaced   out\twords\n") == 3


def test_top_words():
    text = "the cat the dog the bird cat"
    assert top_words(text, 2) == [("the", 3), ("cat", 2)]
    assert top_words("") == []


def test_median_word_length():
    # Odd count: middle value.
    assert median_word_length("a bb ccc") == 2.0
    # Even count: mean of the two middle values.
    assert median_word_length("aa bb cc d") == 2.0
    assert median_word_length("") == 0.0
    assert median_word_length("single") == 6.0
