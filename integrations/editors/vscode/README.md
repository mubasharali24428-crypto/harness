# Harness for VS Code (thin client)

Commands: sign in (token kept in VS Code SecretStorage), select project, run status, review change sets (accept/reject hunks), approve plan (explicit confirmation).
It talks only to the configured Harness services; it never merges, never executes tools and never stores the token in settings or logs. Non-loopback `http://` URLs are refused.
Build/test: `npm ci && npm test && npm run package`.
