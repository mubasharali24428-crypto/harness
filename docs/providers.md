# Providers

Who the harness can talk to, how a model id becomes an HTTP request, and what
"we accept any valid key" actually guarantees.

## The registry

`section3/src/harness_router/provider_registry.py` is the single list. As of
2026-10-25 it holds **65 providers / 117 named models** across 9 regions, 46 of
them serving open weights and 8 running on your own machine:

| Group | Examples | Count |
|-------|----------|-------|
| Frontier labs | OpenAI, Anthropic, Google, Mistral, xAI, Cohere, Meta, DeepSeek, AI21, Aleph Alpha, Writer, Upstage, Reka, Alibaba (Qwen) | 15 |
| Open-weight hosts | Together, Fireworks, Groq, Cerebras, SambaNova, DeepInfra, Nebius, Lambda, Hyperbolic, Anyscale, Novita, Featherless, Segmind, Hugging Face, ModelScope, Replicate | 16 |
| Chinese labs | Moonshot, Zhipu, Baichuan, StepFun, MiniMax, 01.AI, Volcengine, Baidu, Tencent, iFlytek, SenseTime, SiliconFlow | 12 |
| Gateways & clouds | OpenRouter, Perplexity, AI/ML API, AiHubMix, 302.AI, Cloudflare, OVH, Scaleway, NVIDIA, Azure, Bedrock, Vertex, Databricks, Snowflake | 14 |
| Local runtimes | Ollama, vLLM, llama.cpp, LM Studio, LocalAI, Jan, KoboldCpp, TGI | 8 |

The list is a **convenience, not a permission**. `POST /api/v1/keys` validates
the provider *shape* only
(`section1/src/harness_identity/api_keys.py:42`), so a key for a lab we have
never heard of is accepted, and `GET /api/v1/models/prefs` accepts any model id
whose provider prefix is routable. Sixty-five is a starting set, not a ceiling;
adding one is a data edit.

## How a model id becomes a request

`resolve_endpoint(model)` picks the URL and wire protocol. First match wins:

1. `HARNESS_PROVIDER_BASE_URL` — a deployment-wide gateway (OpenRouter,
   LiteLLM, a corporate proxy). Protocol is forced to OpenAI, because a gateway
   serves every vendor through one OpenAI-compatible surface.
2. The provider's own `*_BASE_URL` (e.g. `GROQ_BASE_URL`) — a per-vendor
   override. The provider's **native protocol is kept**, since you pointed at
   that vendor's own API.
3. `DEEPSEEK_BASE_URL` — the historical single-endpoint deployment variable,
   treated as a gateway. Existing installs and test suites keep working.
4. The registry's documented URL, native protocol.
5. An unregistered provider falls back to the deployment endpoint rather than
   failing.

Three protocols are implemented: OpenAI `chat/completions`, Anthropic
`/v1/messages` (`x-api-key` + `anthropic-version`, system prompt lifted out,
`max_tokens` defaulted because the API requires it), and Gemini
`:generateContent` (model in the path, `x-goog-api-key`, `assistant`→`model`
role mapping). Everything else in the registry speaks OpenAI.

Before this, the router had **one** outbound target whatever the model was:
`DEEPSEEK_BASE_URL`. The six "providers" in the old catalog were six labels on
one URL.

## Keys and secrets

The raw key exists only inside the outbound `Authorization` / `x-api-key` /
`x-goog-api-key` header. It never enters a log, an exception message or a
`ModelResult` (`ModelProviderClient._scrub` is belt-and-braces on anything
surfaced). At rest it is Fernet-encrypted under `HARNESS_MASTER_KEY`; the wire
and the UI carry a masked hint only.

## Pricing honesty

`MODEL_PRICING_CATALOG` in frozen `contracts/` prices 15 models. For anything
else `PricingEngine` silently returns $0.50/$1.50 per million — which, at 117
model ids across 65 providers, would have invented spend for free local runs
and under-billed frontier models by an order of magnitude.

So the router bills through `providers.priced_cost()`: a priced model gets its
exact rate; an unpriced one bills **zero** and logs the gap. The catalog marks
each provider's `priced` list and the picker shows "cost not tracked" rather
than implying a number. Adding a real rate card per provider is open work
(tracked in `docs/backlog.md`).

## Two things this does not yet do

* **Egress.** The sandbox reaches providers through `harness-sandbox-egress`,
  whose allow-list predates this registry. A new provider is routable from S3
  but not necessarily from inside a confined run — that list has to be derived
  from the registry, and that is a network-policy change needing sign-off.
* **Per-team endpoint override.** Teams can choose a provider and a model; they
  cannot yet point a provider at their own base URL (Azure resource, Bedrock
  region). That stays an operator env variable, deliberately: a team-supplied
  URL is an SSRF surface.
