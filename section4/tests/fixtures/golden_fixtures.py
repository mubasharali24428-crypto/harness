"""Golden request/response fixtures for Section 4 Conformance Tests."""

GOLDEN_RUN_ID = "run-golden-401"
GOLDEN_USER_ID = "user-eng-001"
GOLDEN_GOAL = "Implement user authentication middleware and unit tests."

GOLDEN_DIFF_CLEAN = """--- a/auth.py
+++ b/auth.py
@@ -1,1 +1,2 @@
 # auth implementation
+def verify_token(token: str) -> bool: return True
"""

GOLDEN_DIFF_CONFLICT = """--- a/auth.py
+++ b/auth.py
@@ -1,1 +1,3 @@
<<<<<<< HEAD
def old_auth(): pass
=======
def new_auth(): pass
>>>>>>> branch
"""
