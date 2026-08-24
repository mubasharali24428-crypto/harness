"""Suite-wide test environment for section3.

FIX-LOOP E1: outside an explicitly declared dev environment, keyless provider
calls now raise honestly (503 PROVIDER_UNAVAILABLE) instead of synthesizing a
"Simulated response ...". The historical simulated replies are kept ONLY as an
explicit dev convenience, so this suite declares itself dev — exactly what a
developer running pytest locally opts into, and NOT a weakening of prod
behavior (prod never sets this env var; the real-key path is unaffected).

The apiKey-conformance tests additionally cover both gate states themselves.
"""

import os

os.environ.setdefault("HARNESS_ENV", "dev")
