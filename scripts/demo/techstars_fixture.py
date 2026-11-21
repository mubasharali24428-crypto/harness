#!/usr/bin/env python3
"""Create a fresh, isolated failing-test workspace for a local demo."""
from pathlib import Path
import tempfile


def main():
    workspace = Path(tempfile.mkdtemp(prefix="harness-techstars-"))
    (workspace / "calc.py").write_text('def add(a, b):\n    return a - b\n')
    (workspace / "test_calc.py").write_text(
        'import unittest\n\nfrom calc import add\n\n\n'
        'class AddTest(unittest.TestCase):\n'
        '    def test_add(self):\n'
        '        self.assertEqual(add(2, 3), 5)\n\n\n'
        'if __name__ == "__main__":\n    unittest.main()\n'
    )
    print(workspace)


if __name__ == "__main__":
    main()
