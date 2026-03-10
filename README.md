# astrbot_plugin_chat_tool_balance

Chat Tool Balance is an AstrBot plugin MVP that keeps tool-first behavior while preserving topic-aware short-term memory and long-term summary sync.

## Features

- Tool-first orchestration with chat fallback.
- Image OCR stage with permanent description cache.
- Topic routing with single-message single-topic guarantee.
- Bucketed short-memory persistence and context window builder.
- Hybrid summary trigger (message count + silence time).
- LivingMemory v2 bridge with pending-sync retry.

## Configuration

Core config is defined in `_conf_schema.json`:

- `models.chat_default`
- `models.ocr`
- `models.topic_classifier`
- `models.tool_intent_classifier`
- `models.summary`
- `summary.enabled`
- `summary.trigger_non_bot_count`
- `summary.trigger_silence_minutes`
- `storage.base_dir`
- `storage.bucket_count`

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
