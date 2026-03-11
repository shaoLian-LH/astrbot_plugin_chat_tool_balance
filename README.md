# astrbot_plugin_chat_tool_balance

Chat Tool Balance is an AstrBot plugin MVP that keeps tool-first behavior while preserving topic-aware short-term memory and long-term summary sync.

## Features

- Tool-first orchestration with chat fallback.
- Image OCR stage with permanent description cache.
- Topic routing with single-message single-topic guarantee.
- Bucketed short-memory persistence and context window builder.
- Hybrid summary trigger (message count + silence time).
- LivingMemory v2 bridge with pending-sync retry.
- Unified `LLMGateway`: all roles prefer Responses API and auto fallback.
- Chat chain keeps `previous_response_id` state; non-chat roles are stateless.
- Summary `completed` event clears `response_state` for the same `scope_id + topic_id`.

## Configuration

Core config is defined in `_conf_schema.json`:

- `models.chat_default`
- `models.ocr`
- `models.topic_classifier`
- `models.tool_intent_classifier`
- `models.summary`
- `models.chat_model`
- `models.ocr_model`
- `models.topic_classifier_model`
- `models.tool_intent_classifier_model`
- `models.summary_model`
- `features.use_responses_api`
- `summary.enabled`
- `summary.trigger_non_bot_count`
- `summary.trigger_silence_minutes`
- `storage.base_dir`
- `storage.bucket_count`

Role-to-provider mapping:

- `models.chat_default`: default chat provider id.
- `models.ocr`: OCR provider id (fallbacks to `models.chat_default` when empty).
- `models.topic_classifier`: topic classification provider id.
- `models.tool_intent_classifier`: tool-intent classification provider id.
- `models.summary`: summary generation provider id.

Optional Responses model mapping (decouples provider id from `responses.create(model=...)`):

- `models.chat_model`: chat role model name used by Responses.
- `models.ocr_model`: OCR role model name (fallback to `models.chat_model` when empty).
- `models.topic_classifier_model`: topic role model name.
- `models.tool_intent_classifier_model`: tool-intent role model name.
- `models.summary_model`: summary role model name.

`features.use_responses_api` behavior:

- `true` (default): `chat/ocr/topic_classifier/tool_intent_classifier/summary` all try Responses first.
- `false`: all roles use AstrBot `llm_generate` fallback path directly.

Minimal config example:

```yaml
features:
  use_responses_api: true
models:
  chat_default: provider-openai
  ocr: provider-openai
  topic_classifier: provider-openai
  tool_intent_classifier: provider-openai
  summary: provider-openai
  chat_model: gpt-4.1
  ocr_model: gpt-4.1-mini
summary:
  enabled: true
```

## Data Storage

Default base path:

`/data/plugin_data/astrbot_plugin_chat_tool_balance`

Storage is split into 10 buckets for short memory and image cache.

## Development

Run tests:

```bash
pytest -q
```

## References

- [AstrBot Repo](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot Plugin Development Docs (Chinese)](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot Plugin Development Docs (English)](https://docs.astrbot.app/en/dev/star/plugin-new.html)
