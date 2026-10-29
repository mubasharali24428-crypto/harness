"""Golden request/response fixtures for Section 3 Conformance Tests."""

GOLDEN_AGENT_ID = "agent-primary-001"
GOLDEN_MESSAGES = [
    {"role": "system", "content": "You are a professional software engineer."},
    {"role": "user", "content": "Write a python function to compute fibonacci numbers."},
]

GOLDEN_EXPECTED_MODELS = [
    "deepseek/deepseek-chat",
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-r1-0528",
    "qwen/qwen-2.5-coder-32b-instruct",
    "qwen/qwen-2.5-72b-instruct",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "anthropic/claude-sonnet-4.5",
    "anthropic/claude-haiku-4.5",
    "mistralai/mistral-large-2512",
    "mistralai/codestral-2508",
    "local/qwen3:14b-ablated",
    "local/custom",
]
