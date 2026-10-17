"""Stable, code-bearing errors: callers branch on ``code``, never on text."""


class RuntimeDenied(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
