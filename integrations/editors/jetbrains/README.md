# Harness for JetBrains IDEs

There is **no compiled JetBrains plugin** (no JDK/Gradle/IDE in the build environment, and unverified Kotlin was not shipped).
The supported path today is the IDE's built-in HTTP Client: open `harness.http`, choose the `local` environment, and put
`{"local": {"token": "…", "projectId": "…"}}` in `http-client.private.env.json` (git-ignore it).
`tests/test_editor_artifacts.py` checks every request in `harness.http` against the real service's OpenAPI.
Status: F002 PARTIAL — protocol-level access only; no tool window, no host validation.
