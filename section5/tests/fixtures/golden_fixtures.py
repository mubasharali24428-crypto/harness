"""Golden request/response fixtures for Section 5 Conformance Tests."""

GOLDEN_TASK_ID = "task-golden-101"
GOLDEN_REPO_REF = "github.com/example/demo-repo.git"
GOLDEN_BRANCH = "agent/feature-login"
GOLDEN_FORBIDDEN_PATHS = ["auth/secrets.py", "migrations/sensitive/"]

GOLDEN_DIFF_HELLO = """--- a/hello.py
+++ b/hello.py
@@ -1,1 +1,2 @@
 print("hello")
+print("world")
"""

GOLDEN_DIFF_NEW_FILE = """--- /dev/null
+++ b/new_module.py
@@ -0,0 +1,3 @@
+def add(a, b):
+    return a + b
"""

GOLDEN_PYTEST_OUTPUT = """=== test session starts ===
collected 5 items

test_auth.py ..                                                          [ 40%]
test_api.py .F.                                                          [100%]

=================================== FAILURES ===================================
_________________________________ test_login ___________________________________
    def test_login():
>       assert False
E       assert False
test_api.py:12: AssertionError
=========================== short test summary info ============================
FAILED test_api.py::test_login - assert False
========================= 1 failed, 4 passed in 0.45s ==========================
"""
