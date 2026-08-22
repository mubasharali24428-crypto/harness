"""Golden fixtures for Section 6 conformance tests."""

GOLDEN_PROJECT_ID = "proj-golden-601"
GOLDEN_MEETING_TITLE = "Sprint Planning: Auth Module"
GOLDEN_USER_IDS = ["user-eng-001", "user-eng-002", "user-lead-001"]

GOLDEN_MESSAGES = [
    ("user-eng-001", "We need to implement JWT authentication for the API gateway."),
    ("user-eng-002", "What about token refresh? That's a requirement for mobile clients."),
    (
        "user-lead-001",
        "Agreed. Token refresh is a must. Also, consider rate limiting as a constraint.",
    ),
    ("user-eng-001", "@ai What are the best practices for JWT token refresh?"),
    (
        "user-eng-002",
        "The acceptance criteria should include: all auth endpoints return proper 401/403 codes.",
    ),
    ("user-lead-001", "Risk: if we break the auth middleware, every service is affected."),
    ("user-eng-001", "Open question: do we support OAuth2 social login in this sprint?"),
]
