"""A boundary a review should notice: `retry` gains a silent default."""


def retry(times: int) -> str:
    return f"retrying {times} time(s)"
