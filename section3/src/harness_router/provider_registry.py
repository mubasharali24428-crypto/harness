"""The provider registry: every endpoint the harness can talk to.

Why this module exists
----------------------
Until the 2026-10-25 audit the router had ONE outbound target: whatever
``DEEPSEEK_BASE_URL`` pointed at, no matter which model was named. "Six
providers" in the catalog meant six labels on one URL. This registry is the
data that lets the router actually route: base URL, wire protocol, auth style
and a key env per provider, plus the model ids that live behind it.

Rules this module keeps to
--------------------------
* Declarative only — no network, no secrets read at import time.
* A provider is listed when its documented endpoint is OpenAI-compatible
  (``protocol="openai"``), Anthropic-native (``messages``), or Google-native
  (``generateContent``). Local/self-hosted runtimes get ``local=True`` and no
  key env.
* Model ids are ``provider/wire-name``; ``wire_name()`` strips the prefix at
  the edge. Only ids we are sure exist are listed — an empty ``models`` tuple
  means "we know the endpoint, not the catalogue", which is still usable
  because the picker takes free text.
* The list is a convenience, not a permission: ``POST /api/v1/keys`` accepts
  any provider string, and an unlisted model resolves through the deployment
  endpoint. Fifty entries is a starting set, not a ceiling.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

Protocol = Literal["openai", "anthropic", "gemini"]

# Deployment-wide override: every provider call goes here instead. This is the
# gateway case (OpenRouter, a corporate proxy, an LiteLLM-style router) where
# one URL serves many vendors, and it is also what the existing single-endpoint
# deployments and test suites rely on.
GLOBAL_BASE_URL_ENV = "HARNESS_PROVIDER_BASE_URL"
LEGACY_BASE_URL_ENV = "DEEPSEEK_BASE_URL"

ANTHROPIC_VERSION = "2023-06-01"


@dataclass(frozen=True)
class Provider:
    """One reachable model provider."""

    name: str
    label: str
    protocol: Protocol = "openai"
    base_url: str = ""
    base_url_env: str | None = None
    api_key_env: str | None = None
    docs_url: str = ""
    region: str = "global"
    local: bool = False
    open_weights: bool = False
    #: True when the documented URL embeds something only the operator knows
    #: (an Azure resource, an AWS region + account, a GCP project, a Snowflake
    #: account). These are listed so a team can find them, but the router can
    #: only reach them once `*_BASE_URL` is set — never pretend otherwise.
    requires_own_endpoint: bool = False
    models: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "label": self.label,
            "protocol": self.protocol,
            "baseUrl": self.base_url,
            "baseUrlEnv": self.base_url_env,
            "apiKeyEnv": self.api_key_env,
            "docsUrl": self.docs_url,
            "region": self.region,
            "local": self.local,
            "openWeights": self.open_weights,
            "requiresOwnEndpoint": self.requires_own_endpoint,
            "models": list(self.models),
        }


def _p(
    name: str,
    label: str,
    *,
    protocol: Protocol = "openai",
    base_url: str = "",
    base_url_env: str | None = None,
    api_key_env: str | None = None,
    docs_url: str = "",
    region: str = "global",
    local: bool = False,
    open_weights: bool = False,
    requires_own_endpoint: bool = False,
    models: tuple[str, ...] = (),
) -> Provider:
    env = api_key_env or f"{name.upper().replace('-', '_')}_API_KEY"
    return Provider(
        name=name,
        label=label,
        protocol=protocol,
        base_url=base_url,
        base_url_env=base_url_env or f"{name.upper().replace('-', '_')}_BASE_URL",
        api_key_env=None if local else env,
        docs_url=docs_url,
        region=region,
        local=local,
        open_weights=open_weights,
        requires_own_endpoint=requires_own_endpoint,
        models=models,
    )


# ---------------------------------------------------------------------------
# Frontier labs — closed weights, own endpoints.
# ---------------------------------------------------------------------------
FRONTIER: tuple[Provider, ...] = (
    _p(
        "openai", "OpenAI", base_url="https://api.openai.com/v1",
        docs_url="https://platform.openai.com/docs/api-reference", region="us",
        models=(
            "openai/gpt-4.1", "openai/gpt-4.1-mini", "openai/o3", "openai/o4-mini",
            "openai/gpt-4o", "openai/gpt-4o-mini",
        ),
    ),
    _p(
        "anthropic", "Anthropic", protocol="anthropic",
        base_url="https://api.anthropic.com",
        # Anthropic moved the console docs off docs.anthropic.com; the old URL
        # 301s to platform.claude.com, which an egress allow-list derived from
        # this registry would then refuse (surfaced by W4's live drill).
        docs_url="https://platform.claude.com/docs/en/api/messages", region="us",
        models=(
            "anthropic/claude-opus-4-1", "anthropic/claude-sonnet-4",
            "anthropic/claude-3-7-sonnet", "anthropic/claude-3-5-haiku",
        ),
    ),
    _p(
        "google", "Google Gemini", protocol="gemini",
        base_url="https://generativelanguage.googleapis.com",
        docs_url="https://ai.google.dev/gemini-api/docs", region="us",
        models=(
            "google/gemini-2.5-pro", "google/gemini-2.5-flash",
            "google/gemini-2.0-flash",
        ),
    ),
    _p(
        "mistral", "Mistral AI", base_url="https://api.mistral.ai/v1",
        docs_url="https://docs.mistral.ai/api", region="eu",
        models=("mistral/mistral-large-latest", "mistral/mistral-small-latest",
                "mistral/codestral-latest", "mistral/devstral-small-latest"),
    ),
    _p(
        "xai", "xAI (Grok)", base_url="https://api.x.ai/v1",
        docs_url="https://docs.x.ai/docs/api-reference", region="us",
        models=("xai/grok-4", "xai/grok-3", "xai/grok-3-mini"),
    ),
    _p(
        "cohere", "Cohere", base_url="https://api.cohere.com/v2",
        docs_url="https://docs.cohere.com/reference/about", region="us",
        open_weights=True,
        models=("cohere/command-a", "cohere/command-r-08-2024", "cohere/nora"),
    ),
    _p(
        "meta", "Meta (Llama API)", base_url="https://api.llama.com/compat/v1",
        docs_url="https://developer.meta.com/llama/docs", region="us",
        open_weights=True,
        models=("meta/llama-4-maverick", "meta/llama-4-scout", "meta/llama-3.3-70b"),
    ),
    _p(
        "ai21", "AI21 Labs", base_url="https://api.ai21.com/studio/v1",
        docs_url="https://docs.ai21.com/reference", region="il",
        models=("ai21/maestro-reasoning", "ai21/jamba-1-7-large"),
    ),
    _p(
        "aleph-alpha", "Aleph Alpha", base_url="https://api.aleph-alpha.com/chat/v1",
        docs_url="https://docs.aleph-alpha.com/api", region="eu",
        models=("aleph-alpha/aleph-alpha-3-5-36b", "aleph-alpha/lumimaid-x-70b"),
    ),
    _p(
        "writer", "Writer", base_url="https://api.writer.com/v1/chat/completions",
        docs_url="https://dev.writer.com/api-reference", region="us",
        models=("writer/palmyra-x5", "writer/palmyra-x4"),
    ),
    _p(
        "upstage", "Upstage (Solar)", base_url="https://api.upstage.ai/v1/solar",
        docs_url="https://console.upstage.ai/docs/capabilities/quickstart", region="kr",
        models=("upstage/solar-pro", "upstage/solar-mini"),
    ),
    _p(
        "reka", "Reka AI", base_url="https://api.reka.ai/v1/chat",
        docs_url="https://docs.reka.ai/api-reference", region="us",
        models=("reka/core-3", "reka/flash-3"),
    ),
    _p(
        "contextual", "Contextual AI", base_url="https://api.contextual.ai/llm/v1",
        docs_url="https://docs.contextual.ai", region="us", models=(),
    ),
    _p(
        "deepseek", "DeepSeek", base_url="https://api.deepseek.com",
        base_url_env="DEEPSEEK_BASE_URL", api_key_env="DEEPSEEK_API_KEY",
        docs_url="https://api-docs.deepseek.com", region="cn", open_weights=True,
        models=("deepseek/deepseek-chat", "deepseek/deepseek-coder",
                "deepseek/deepseek-reasoner"),
    ),
    _p(
        "qwen", "Alibaba Cloud (Qwen / DashScope)",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="DASHSCOPE_API_KEY",
        docs_url="https://www.alibabacloud.com/help/en/model-studio", region="cn",
        open_weights=True,
        models=("qwen/qwen-plus", "qwen/qwen-turbo", "qwen/qwen2.5-coder-32b-instruct"),
    ),
)

# ---------------------------------------------------------------------------
# Open-weight hosts — serving weights that are free to download.
# ---------------------------------------------------------------------------
OPEN_WEIGHT_HOSTS: tuple[Provider, ...] = (
    _p(
        "together", "Together AI", base_url="https://api.together.xyz/v1",
        docs_url="https://docs.together.ai/reference", region="us", open_weights=True,
        models=("together/meta-llama/Llama-3.3-70B-Instruct-Turbo",
                "together/deepseek-ai/DeepSeek-V3",
                "together/Qwen/Qwen2.5-Coder-32B-Instruct"),
    ),
    _p(
        "fireworks", "Fireworks AI", base_url="https://api.fireworks.ai/inference/v1",
        docs_url="https://readme.fireworks.ai", region="us", open_weights=True,
        models=("fireworks/accounts/fireworks/models/llama-v3p3-70b-instruct",
                "fireworks/accounts/fireworks/models/deepseek-v3"),
    ),
    _p(
        "groq", "Groq", base_url="https://api.groq.com/openai/v1",
        docs_url="https://console.groq.com/docs/api-reference", region="us",
        open_weights=True,
        models=("groq/llama-3.3-70b-versatile", "groq/llama-3.1-8b-instant",
                "groq/deepseek-r1-distill-llama-70b"),
    ),
    _p(
        "cerebras", "Cerebras Inference", base_url="https://api.cerebras.ai/v1",
        docs_url="https://inference-docs.cerebras.ai", region="us", open_weights=True,
        models=("cerebras/llama-3.3-70b", "cerebras/qwen-2.5-14b-instruct-122025"),
    ),
    _p(
        "sambanova", "SambaNova (Scaffold)", base_url="https://api.sambanova.ai/v1",
        docs_url="https://docs.sambanova.ai/cloud/api-reference", region="us",
        open_weights=True,
        models=("sambanova/DeepSeek-R1", "sambanova/Llama-3.3-70B-Instruct"),
    ),
    _p(
        "deepinfra", "DeepInfra", base_url="https://api.deepinfra.com/v1/openai",
        docs_url="https://deepinfra.com/docs", region="us", open_weights=True,
        models=("deepinfra/meta-llama/Llama-3.3-70B-Instruct",
                "deepinfra/deepseek-ai/DeepSeek-V3"),
    ),
    _p(
        "nebius", "Nebius AI Studio", base_url="https://api.studio.nebius.ai/v1",
        docs_url="https://docs.nebius.com/llm/text", region="eu", open_weights=True,
        models=("nebius/meta-llama/Llama-3.3-70B-Instruct",
                "nebius/deepseek-ai/DeepSeek-V3"),
    ),
    _p(
        "lambdalabs", "Lambda Inference", base_url="https://api.lambdalabs.com/v1",
        docs_url="https://docs.lambdalabs.com/cloud/api-reference", region="us",
        open_weights=True, models=("lambdalabs/llama3.3-70b-instruct",),
    ),
    _p(
        "hyperbolic", "Hyperbolic Labs", base_url="https://api.hyperbolic.xyz/v1",
        docs_url="https://hyperbolic-labs.github.io/product-docs", region="us",
        open_weights=True, models=("hyperbolic/meta-llama/Llama-3.3-70B-Instruct",),
    ),
    _p(
        "anyscale", "Anyscale Endpoints", base_url="https://api.endpoints.anyscale.com/v1",
        docs_url="https://docs.anyscale.com/endpoints", region="us", open_weights=True,
        models=("anyscale/meta-llama/Llama-3.3-70B-Instruct",),
    ),
    _p(
        "novita", "Novita AI", base_url="https://api.novita.ai/openai",
        docs_url="https://docs.novita.ai/novita/openai", region="sg",
        open_weights=True, models=("novita/deepseek/deepseek-r1",),
    ),
    _p(
        "featherless", "Featherless AI", base_url="https://featherless.ai/v1",
        docs_url="https://featherless.ai/docs", region="eu", open_weights=True,
        models=("featherless/ai21labs/Aya-Expanse-32B",),
    ),
    _p(
        "segmind", "Segmind Vega", base_url="https://api.segmind.com/v1",
        docs_url="https://segmind.com/docs", region="in", open_weights=True,
        models=("segmind/llama-3-8b-instruct",),
    ),
    _p(
        "huggingface", "Hugging Face Inference", base_url="https://router.huggingface.co/v1",
        docs_url="https://huggingface.co/docs/inference-providers", region="eu",
        open_weights=True,
        models=("huggingface/meta-llama/Llama-3.3-70B-Instruct",
                "huggingface/deepseek-ai/DeepSeek-V3"),
    ),
    _p(
        "modelscope", "ModelScope (China)", base_url="https://api-inference.modelscope.cn/v1",
        docs_url="https://www.modelscope.cn/docs", region="cn", open_weights=True,
        models=("modelscope/Qwen/Qwen2.5-72B-Instruct",),
    ),
    _p(
        "replicate", "Replicate", base_url="https://api.replicate.com/v1/openai",
        docs_url="https://replicate.com/docs/language-models", region="us",
        open_weights=True, models=("replicate/meta/llama-3.3-70b-instruct",),
    ),
)

# ---------------------------------------------------------------------------
# Chinese labs — the open-weight ecosystem outside the US/EU axis.
# ---------------------------------------------------------------------------
CHINA: tuple[Provider, ...] = (
    _p(
        "moonshot", "Moonshot AI (Kimi)", base_url="https://api.moonshot.cn/v1",
        docs_url="https://platform.moonshot.ai/docs", region="cn", open_weights=True,
        models=("moonshot/kimi-k2-0905-preview", "moonshot/moonshot-v1-128k"),
    ),
    _p(
        "zhipu", "Zhipu (GLM)", base_url="https://open.bigmodel.cn/api/paas/v4",
        docs_url="https://docs.z.ai/guides/overview/pricing", region="cn",
        open_weights=True, models=("zhipu/glm-4.5", "zhipu/glm-4-plus"),
    ),
    _p(
        "baichuan", "Baichuan", base_url="https://api.baichuan-ai.com/v1",
        docs_url="https://platform.baichuan-ai.com/docs/api", region="cn",
        open_weights=True, models=("baichuan/baichuan4-turbo",),
    ),
    _p(
        "stepfun", "StepFun", base_url="https://api.stepfun.com/v1",
        docs_url="https://platform.stepfun.com/docs", region="cn", open_weights=True,
        models=("stepfun/step-3",),
    ),
    _p(
        "minimax", "MiniMax", base_url="https://api.minimax.io/v1",
        docs_url="https://www.minimaxi.com/document", region="cn",
        models=("minimax/MiniMax-Text-01", "minimax/abab6.5s-chat"),
    ),
    _p(
        "lingyiwanwu", "01.AI (Yi)", base_url="https://api.lingyiwanwu.com/v1",
        docs_url="https://platform.lingyiwanwu.com/docs", region="cn",
        open_weights=True, models=("lingyiwanwu/yi-lightning", "lingyiwanwu/yi-vision"),
    ),
    _p(
        "volcengine", "Volcengine Ark (Doubao)",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        docs_url="https://www.volcengine.com/docs/82379", region="cn",
        models=("volcengine/doubao-pro-32k",),
    ),
    _p(
        "baidu", "Baidu (ERNIE / Qianfan)", base_url="https://qianfan.baidubce.com/v2",
        docs_url="https://cloud.baidu.com/doc/qianfan/s/1hmdpag6e", region="cn",
        models=("baidu/ernie-4.5-turbo-128k",),
    ),
    _p(
        "tencent", "Tencent Hunyuan", base_url="https://api.hunyuan.cloud.tencent.com/v1",
        docs_url="https://cloud.tencent.com/document/product/1729", region="cn",
        open_weights=True, models=("tencent/hunyuan-turbo", "tencent/hunyuan-a13b-instruct"),
    ),
    _p(
        "xfyun", "iFlytek Spark", base_url="https://spark-api-open.xf-yun.com/v1",
        docs_url="https://www.xfyun.cn/doc/spark/api-http.html", region="cn",
        models=("xfyun/spark4.0-ultra-125k",),
    ),
    _p(
        "sensetime", "SenseTime (SenseNova)", base_url="https://token.sensenova.cn/v1",
        docs_url="https://platform.sensenova.cn/docs", region="cn", open_weights=True,
        models=("sensetime/sensenova-6.5-flash",),
    ),
    _p(
        "siliconflow", "SiliconFlow", base_url="https://api.siliconflow.com/v1",
        docs_url="https://docs.siliconflow.cn", region="cn", open_weights=True,
        models=("siliconflow/deepseek-ai/DeepSeek-V3",
                "siliconflow/Qwen/Qwen2.5-72B-Instruct"),
    ),
)

# ---------------------------------------------------------------------------
# Gateways and clouds — one endpoint, many vendors behind it.
# ---------------------------------------------------------------------------
GATEWAYS: tuple[Provider, ...] = (
    _p(
        "openrouter", "OpenRouter", base_url="https://openrouter.ai/api/v1",
        docs_url="https://openrouter.ai/docs/api-reference", region="global",
        open_weights=True,
        models=("openrouter/deepseek/deepseek-chat",
                "openrouter/anthropic/claude-sonnet-4",
                "openrouter/meta-llama/llama-3.3-70b-instruct"),
    ),
    _p(
        "perplexity", "Perplexity (Sonar)", base_url="https://api.perplexity.ai",
        docs_url="https://docs.perplexity.ai/api-reference", region="us",
        models=("perplexity/sonar-pro", "perplexity/llama-3.1-sonar-large-128k-online"),
    ),
    _p(
        "aimlapi", "AI/ML API", base_url="https://api.aimlapi.com/v1",
        docs_url="https://docs.aimlapi.com/api-references", region="global",
        open_weights=True, models=("aimlapi/openai/gpt-4o-mini",),
    ),
    _p(
        "aihubmix", "AiHubMix", base_url="https://api.aihubmix.com/v1",
        docs_url="https://docs.aihubmix.com/api", region="cn", open_weights=True,
        models=("aihubmix/gpt-4o",),
    ),
    _p(
        "threex02ai", "302.AI", base_url="https://api.302.ai/v1",
        docs_url="https://doc.302.ai", region="cn", open_weights=True,
        models=("threex02ai/claude-sonnet-4",),
    ),
    _p(
        "cloudflare", "Cloudflare Workers AI",
        base_url="https://api.cloudflare.com/client/v4/accounts",
        requires_own_endpoint=True,
        docs_url="https://developers.cloudflare.com/workers-ai", region="global",
        open_weights=True, models=("cloudflare/@cf/meta/llama-3.3-70b-instruct-fp8-fast",),
    ),
    _p(
        "ovhcloud", "OVHcloud AI Endpoints", base_url="https://api.endpoints.ovhcloud.com/v1",
        docs_url="https://docs.ovhcloud.com/guides/public-cloud/ai-machine-learning",
        region="eu", open_weights=True, models=("ovhcloud/gpt-oss-120b",),
    ),
    _p(
        "scaleway", "Scaleway Generative APIs",
        base_url="https://kb.fr-par.scw.cloud/v1",
        docs_url="https://www.scaleway.com/en/docs/generative-apis", region="eu",
        open_weights=True, models=("scaleway/meta/llama-3.3-70b-instruct",),
    ),
    _p(
        "nvidia", "NVIDIA NIM", base_url="https://integrate.api.nvidia.com/v1",
        docs_url="https://docs.nvidia.com/nim", region="us", open_weights=True,
        models=("nvidia/meta/llama-3.3-70b-instruct", "nvidia/deepseek-ai/deepseek-v3"),
    ),
    _p(
        "azure", "Azure OpenAI",
        base_url="https://REPLACE-WITH-YOUR-RESOURCE.openai.azure.com",
        requires_own_endpoint=True,
        docs_url="https://learn.microsoft.com/azure/ai-services/openai", region="global",
        models=("azure/gpt-4o", "azure/o3-mini"),
    ),
    _p(
        "bedrock", "AWS Bedrock",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        requires_own_endpoint=True,
        docs_url="https://docs.aws.amazon.com/bedrock", region="us", open_weights=True,
        models=("bedrock/anthropic.claude-sonnet-4", "bedrock/meta.llama3-3-70b-instruct-v1:0"),
    ),
    _p(
        "vertex", "Google Vertex AI",
        base_url="https://us-central1-aiplatform.googleapis.com",
        requires_own_endpoint=True,
        docs_url="https://cloud.google.com/vertex-ai/generative-ai/docs", region="global",
        open_weights=True, models=("vertex/gemini-2.5-pro", "vertex/claude-sonnet-4"),
    ),
    _p(
        "databricks", "Databricks Model Serving",
        base_url="https://REPLACE-WITH-YOUR-WORKSPACE.ai/serving/v1",
        requires_own_endpoint=True,
        docs_url="https://docs.databricks.com/en/machine-learning/model-serving",
        region="global", models=("databricks/dbrx-instruct",),
    ),
    _p(
        "snowflake", "Snowflake Cortex",
        base_url="https://REPLACE-WITH-YOUR-ACCOUNT.snowflakecomputing.com/api/v2",
        requires_own_endpoint=True,
        docs_url="https://docs.snowflake.com/en/user-guide/snowflake-cortex",
        region="global", models=("snowflake/mistral-large2",),
    ),
)

# ---------------------------------------------------------------------------
# Local runtimes — no key, weights on the machine, nothing leaves the host.
# ---------------------------------------------------------------------------
LOCAL: tuple[Provider, ...] = (
    _p("ollama", "Ollama", base_url="http://127.0.0.1:11434/v1", local=True,
       docs_url="https://github.com/ollama/ollama/blob/main/docs/openai.md",
       region="local", open_weights=True,
       models=("ollama/qwen3:14b", "ollama/llama3.2:3b", "ollama/deepseek-r1:8b")),
    _p("vllm", "vLLM (self-hosted)", base_url="http://127.0.0.1:8000/v1", local=True,
       docs_url="https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html",
       region="local", open_weights=True, models=("vllm/Qwen/Qwen2.5-Coder-32B-Instruct",)),
    _p("llama.cpp", "llama.cpp server", base_url="http://127.0.0.1:8080/v1", local=True,
       docs_url="https://github.com/ggml-org/llama.cpp/tree/master/tools/server",
       region="local", open_weights=True, models=("llama.cpp/model",)),
    _p("lmstudio", "LM Studio", base_url="http://127.0.0.1:1234/v1", local=True,
       docs_url="https://lmstudio.ai/docs/app/api", region="local", open_weights=True,
       models=("lmstudio/local-model",)),
    _p("localai", "LocalAI", base_url="http://127.0.0.1:8080/v1", local=True,
       docs_url="https://localai.io/features/text-generation", region="local",
       open_weights=True, models=("localai/qwen2.5-coder",)),
    _p("jan", "Jan", base_url="http://127.0.0.1:1337/v1", local=True,
       docs_url="https://docs.jan.ai/llama-cpp", region="local", open_weights=True,
       models=("jan/local-model",)),
    _p("koboldcpp", "KoboldCpp", base_url="http://127.0.0.1:5001/v1", local=True,
       docs_url="https://github.com/LostRuins/koboldcpp", region="local",
       open_weights=True, models=("koboldcpp/model",)),
    _p("tgi", "Hugging Face TGI", base_url="http://127.0.0.1:8080/v1", local=True,
       docs_url="https://huggingface.co/docs/text-generation-inference", region="local",
       open_weights=True, models=("tgi/model",)),
)

ALL_PROVIDERS: tuple[Provider, ...] = (
    FRONTIER + OPEN_WEIGHT_HOSTS + CHINA + GATEWAYS + LOCAL
)

_BY_NAME = {p.name: p for p in ALL_PROVIDERS}


def providers() -> tuple[Provider, ...]:
    """Every registered provider, in declaration order."""
    return ALL_PROVIDERS


def get(name: str) -> Provider | None:
    return _BY_NAME.get(name)


def provider_of(model: str) -> Provider | None:
    """The provider a model id names, by its `provider/` prefix.

    Unknown prefix -> None, which means "not in the registry": the caller falls
    back to the deployment endpoint rather than failing, because the registry
    is a convenience and not a permission.
    """
    if "/" not in model:
        return None
    return _BY_NAME.get(model.split("/", 1)[0])


def wire_name(model: str) -> str:
    """`groq/llama-3.3-70b-versatile` -> `llama-3.3-70b-versatile`."""
    return model.split("/", 1)[-1]


def provider_of_keyed_env(environ: dict[str, str] | None = None) -> list[Provider]:
    """Providers this deployment can actually reach (a key env is populated)."""
    env = environ if environ is not None else dict(os.environ)
    return [p for p in ALL_PROVIDERS if p.local or (p.api_key_env and env.get(p.api_key_env))]


@dataclass(frozen=True)
class Endpoint:
    """Where and how to send one model call."""

    url: str
    protocol: Protocol
    provider: str
    source: str  # "provider-env" | "global-env" | "registry" | "unregistered"


def resolve_endpoint(model: str, environ: dict[str, str] | None = None) -> Endpoint:
    """Pick the outbound URL and wire protocol for a model id.

    Precedence, first match wins:

    1. ``HARNESS_PROVIDER_BASE_URL`` — a deployment-wide gateway. Protocol
       becomes ``openai`` because a gateway serves every vendor through one
       OpenAI-compatible surface.
    2. The provider's own ``*_BASE_URL`` env — a per-vendor override (a proxy,
       a regional endpoint, an Azure resource). The provider's native protocol
       is kept, since the operator pointed at that vendor's own API.
    3. ``DEEPSEEK_BASE_URL`` — the historical single-endpoint deployment var,
       kept working for existing installs and suites. Treated as a gateway, so
       protocol becomes ``openai``.
    4. The registry's documented base URL, with the provider's native protocol.
    5. An unregistered model falls back to the deployment endpoint unchanged.
    """
    env = environ if environ is not None else dict(os.environ)
    provider = provider_of(model)

    global_url = env.get(GLOBAL_BASE_URL_ENV, "").strip()
    if global_url:
        return Endpoint(_chat_url(global_url, "openai"), "openai",
                        provider.name if provider else "gateway", "global-env")

    if provider is not None:
        own = env.get(provider.base_url_env or "", "").strip()
        if own:
            return Endpoint(_chat_url(own, provider.protocol), provider.protocol,
                            provider.name, "provider-env")
        legacy = env.get(LEGACY_BASE_URL_ENV, "").strip()
        if legacy:
            return Endpoint(_chat_url(legacy, "openai"), "openai", provider.name,
                            "legacy-env")
        return Endpoint(_chat_url(provider.base_url, provider.protocol), provider.protocol,
                        provider.name, "registry")

    legacy = env.get(LEGACY_BASE_URL_ENV, "").strip() or env.get(GLOBAL_BASE_URL_ENV, "").strip()
    base = legacy or "https://api.deepseek.com"
    return Endpoint(_chat_url(base, "openai"), "openai", "deployment-default", "unregistered")


def _chat_url(base: str, protocol: Protocol) -> str:
    """Full request URL for one completion call on this protocol."""
    from urllib.parse import urljoin  # local import: keeps module import side-effect free

    base = base.strip().rstrip("/")
    if protocol == "anthropic":
        return base if base.endswith("/v1/messages") else urljoin(base + "/", "v1/messages")
    if protocol == "gemini":
        return base
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"
