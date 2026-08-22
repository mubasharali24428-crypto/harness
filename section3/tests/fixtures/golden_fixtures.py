"""Golden request/response fixtures for Section 3 Conformance Tests."""

GOLDEN_AGENT_ID = "agent-primary-001"
GOLDEN_MESSAGES = [
    {"role": "system", "content": "You are a professional software engineer."},
    {"role": "user", "content": "Write a python function to compute fibonacci numbers."},
]

GOLDEN_EXPECTED_MODELS = [
    "deepseek/deepseek-chat",
    "deepseek/deepseek-coder",
    "deepseek/deepseek-reasoner",
    "qwen/qwen-2.5-coder-32b",
    "qwen/qwen-2.5-72b",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "anthropic/claude-3-5-sonnet",
    "anthropic/claude-3-5-haiku",
    "mistral/mistral-large",
    "mistral/codestral",
    "local/qwen3:14b-ablated",
    "local/custom",
]
