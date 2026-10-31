"""``python -m harness_hx`` — the same entry point as the ``hxc`` console script."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
