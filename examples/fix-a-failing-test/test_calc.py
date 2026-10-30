from calc import increment


def test_each_number_is_incremented():
    assert increment([1, 2, 3]) == [2, 3, 4]


def test_empty_stays_empty():
    assert increment([]) == []
