from policy import retry


def test_retry_reports_the_count():
    assert retry(3) == "retrying 3 time(s)"
