"""The context window a run reports, and the one honest rule about it.

The window the run page shows used to be ``int(os.environ.get("DSH_CONTEXT_WINDOW",
"1000000"))`` in three places. That number is this product's own default, not a
fact about the model: the OpenRouter transport patch declares
``contextWindow: 65536`` for the model in use, and both live drills on
2026-10-26 displayed 1,000,000 anyway. A value nobody configured is a guess, and
presenting a guess as the run's ceiling is the fake affordance roadmap ground
rule 1 forbids.

So: report the window only when an operator actually set it, otherwise report
nothing and let the UI say "window unknown" — a path that already exists in
``StatusPanel`` and has never been reachable until now.
"""

import os

ENV_VAR = "DSH_CONTEXT_WINDOW"


def configured_context_window() -> int | None:
    """The window from the environment, or None when it is absent or unusable.

    None on an unparsable value is deliberate. Falling back to a default there
    was the worst part of the old behaviour: an operator who set
    ``DSH_CONTEXT_WINDOW=1m`` got told 1,000,000 — a number that looked like
    theirs and was not.
    """
    raw = os.environ.get(ENV_VAR)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None
