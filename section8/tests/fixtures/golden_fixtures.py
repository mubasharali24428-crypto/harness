"""Golden request/response fixtures for Section 8 Conformance Tests."""

GOLDEN_PR_ID = "pr-golden-001"
GOLDEN_PROJECT_ID = "proj-golden-001"
GOLDEN_GITHUB_PR_NUMBER = 42
GOLDEN_PR_URL = "https://github.com/org/demo-repo/pull/42"
GOLDEN_RUN_ID = "run-golden-001"
GOLDEN_USER_ID = "user-reviewer-001"

GOLDEN_DIFF = """\
diff --git a/src/auth.py b/src/auth.py
new file mode 100644
index 0000001..abcdefg
--- /dev/null
+++ b/src/auth.py
@@ -0,0 +1,4 @@
+import hashlib
+
+def verify_token(token: str) -> bool:
+    return hashlib.sha256(token.encode()).hexdigest()[:4] == "abcd"
"""

GOLDEN_DIFF_RISK = """\
diff --git a/src/auth/middleware.py b/src/auth/middleware.py
index 1111111..2222222 100644
--- a/src/auth/middleware.py
+++ b/src/auth/middleware.py
@@ -1,5 +1,9 @@
 import os
 from fastapi import HTTPException
-import hashlib
+import subprocess
+
+def check_token(token):
+    if token == os.environ.get("SECRET_KEY"):
+        return True
+    raise HTTPException(401)
"""

GOLDEN_DIFF_MODIFY = """\
diff --git a/src/calc.py b/src/calc.py
index 3333333..4444444 100644
--- a/src/calc.py
+++ b/src/calc.py
@@ -1,4 +1,5 @@
 def add(a, b):
+    # optimized for readability
     return a + b
 
 def mul(a, b):
-    return a * b
+    return a * b  # fixed
"""

GOLDEN_RISK_FILE_PATHS = [
    "src/auth/middleware.py",
    "src/payment/stripe_handler.py",
    "migrations/0003_add_indexes.py",
    ".env.production",
    "secrets/api_keys.py",
    "src/routes/users.py",
    "src/main.py",
]

GOLDEN_RISK_FLAGS_HIGH = [
    "auth_directory",
    "payment_directory",
    "migration_files",
    "env_files",
    "secret_files",
]
