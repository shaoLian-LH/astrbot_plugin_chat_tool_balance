# Chat Tool Balance MVP 设计文档

- 日期: 2026-03-10
- 状态: 已评审通过（用户确认）
- 适用项目: `astrbot_plugin_chat_tool_balance`

## 1. 背景与目标

本插件目标是在 AstrBot 生态中实现“聊天与工具调用平衡”，并优先保障以下能力：

1. 工具优先语义：检测到工具意图时，必须先尝试工具调用；只有工具失败或不可用才降级普通聊天。
2. 多模态上下文增强：重点补齐图片信息理解与长期可用缓存。
3. 主题化群聊/私聊增强：单消息单主题归档，支持主题级短期记忆与总结。
4. 长短期记忆协同：短期记忆本地化，触发总结时再写入 LivingMemory v2。

## 2. 关键约束（已锁定）

1. 主题粒度: 群级共享主题池，单消息只能归属 1 个主题。
2. 会话范围: 群聊与私聊复用同一套逻辑，仅在命名空间与策略上轻分支。
3. 总结触发: 混合触发。
- 非 bot 消息达到阈值（默认 20，可配置）立即总结。
- 否则主题静默达到阈值（默认 60 分钟，可配置）触发总结。
4. 主题识别降级: 模型不可用时走“规则匹配 + 向量最近邻”。
5. 工具增强提示: 仅在检测到工具相关意图时注入最小提示。
6. 模型配置: `OCR / 主题识别 / 工具意图` 均可独立配置，未配置时回退对话默认模型。
7. 图片持久化: 仅持久化 OCR/视觉描述和元数据，不存原图。
8. 本地存储路径硬约束: 所有数据存储在 `/data/plugin_data/astrbot_plugin_chat_tool_balance`。
9. 分桶策略: 使用 10 个分桶对抗长期存储压力（短期记忆与图片缓存均分桶）。

## 3. 总体架构（单插件分层异步流水线）

采用单插件部署，内部拆分阶段化流水线，避免单体同步链路阻塞，并为后续视频理解预留扩展位。

### 3.1 主链路阶段

1. `MessageIngest`
- 接收群聊/私聊事件，标准化为 `NormalizedEvent`。
- 过滤 bot 自身消息（避免污染“非 bot 计数”）。

2. `ImageStage`
- 抽取图片并查永久缓存（分桶 DB）。
- 未命中时调用 OCR/视觉模型并回写缓存。
- 产出 `image_facts` 供后续意图与上下文使用。

3. `ToolIntentStage`（前置）
- 使用独立工具意图模型进行最小化判断。
- 命中则走 `tool-first` 分支；未命中走常规聊天分支。

4. `ToolFirstBranch`
- 注入最小工具增强提示。
- 执行工具调用。
- 成功则基于工具结果生成回复并返回。
- 失败/不可用则降级 `ChatBranch`。

5. `ChatBranch`
- 主题识别与归档（单消息单主题）。
- 写短期记忆（`sqlite + sqlite-vec`，分桶）。
- 按时间跨度/频度/图片信息构建上下文。
- 生成自然聊天回复。

6. `SummaryScheduler`
- 按“计数阈值 + 静默阈值”触发主题总结任务。

7. `LivingMemoryBridge`
- 总结完成后调用 LivingMemory v2 API 入长期记忆。
- 同步失败时保留重试状态，不丢总结结果。

### 3.2 组件清单

1. `main.py`: 生命周期、配置加载、事件订阅、统一异常兜底。
2. `pipeline/orchestrator.py`: 工具优先路由编排。
3. `pipeline/stage_image_ocr.py`: 图片信息处理与永久缓存写入。
4. `pipeline/stage_tool_intent.py`: 工具意图识别。
5. `pipeline/stage_topic_router.py`: 主题识别和降级归档。
6. `pipeline/stage_short_memory.py`: 短期记忆读写与向量检索。
7. `pipeline/stage_context_builder.py`: 上下文构建。
8. `scheduler/summary_scheduler.py`: 触发器与总结任务调度。
9. `bridge/livingmemory_v2_bridge.py`: v2 API 交互。
10. `storage/path_manager.py`: 根路径和分桶路由统一管理。

## 4. 存储设计

### 4.1 目录布局

根目录固定为：

`/data/plugin_data/astrbot_plugin_chat_tool_balance`

建议子目录：

1. `core/`
2. `short_memory/`
3. `summary/`
4. `image/`
5. `image/tmp/`

### 4.2 数据库文件

1. `core/core_state.db`
- `sessions`
- `topics`
- `topic_activity`
- `tool_intent_log`
- `config_snapshot`

2. `short_memory/bucket_00.db` 到 `short_memory/bucket_09.db`
- `messages`
- `topic_message_map`
- `short_summary_cursor`
- 向量表（`sqlite-vec`）: `message_embeddings`

3. `summary/summary_jobs.db`
- `summary_jobs`
- `summary_results`
- `livingmemory_sync_log`

4. `image/cache_00.db` 到 `image/cache_09.db`
- `image_descriptions`
- `image_access_log`

### 4.3 分桶键建议

1. 会话与主题：`hash(scope_id + topic_id) % 10`
2. 图片缓存：`hash(image_content_hash + source_url_hash) % 10`

说明：图片采用“内容哈希 + URL 哈希”联合主键，提升跨 URL 去重和稳定命中率。

## 5. 模型配置契约

```yaml
models:
  chat_default: ""
  ocr: ""
  topic_classifier: ""
  tool_intent_classifier: ""
  summary: ""
```

回退规则：

1. 若 `ocr` 为空，回退 `chat_default`。
2. 若 `topic_classifier` 为空，回退 `chat_default`。
3. 若 `tool_intent_classifier` 为空，回退 `chat_default`。
4. 若 `summary` 为空，回退 `chat_default`。

## 6. 端到端流程与状态机

### 6.1 消息处理主流程（工具优先）

1. 接收并标准化消息。
2. 处理图片并生成 `image_facts`。
3. 执行工具意图判断。
4. 命中工具意图时：
- 注入最小工具提示。
- 尝试工具调用。
- 成功则返回工具增强回复。
- 失败则降级到普通聊天链路。
5. 非工具意图或降级后：
- 主题归档。
- 写短期记忆。
- 构建上下文。
- 生成聊天回复。
6. 更新主题活跃状态与计数器。

### 6.2 主题路由状态机

1. `model_classify`
2. `fallback_rule_match`
3. `fallback_vec_nn`
4. `new_topic_create`
5. `assign_single_topic`

保证：无论模型状态如何，最终都能落入一个主题。

### 6.3 总结触发状态机

1. `counter_trigger`
- 主题内非 bot 消息数 >= `summary.trigger_non_bot_count`（默认 20）即触发。

2. `silence_trigger`
- 若计数未触发，主题静默 >= `summary.trigger_silence_minutes`（默认 60）触发。

3. `summary_execute`
- 聚合主题窗口，调用 `models.summary` 生成总结。

4. `livingmemory_sync`
- 调 LivingMemory v2 写长期记忆。
- 失败则标记 `pending_sync` 并重试。

## 7. 错误处理与降级策略

1. OCR 失败：不阻断链路，记录错误码并保留图片失败占位文本。
2. 主题模型失败：自动降级规则匹配 + 向量最近邻。
3. 工具意图模型失败：回退默认模型，仍失败则进入普通聊天。
4. 工具调用失败：严格执行“先工具后聊天”降级。
5. 短期存储失败：进入内存应急队列异步重试，主回复不中断。
6. LM v2 同步失败：本地保留总结结果并重试，不丢数据。

## 8. 观测与质量门槛

### 8.1 关键指标

1. 时延：`ingest_to_reply_ms`、`tool_intent_ms`、`tool_exec_ms`、`context_build_ms`。
2. 工具路由：`tool_intent_hit_rate`、`tool_success_rate`、`tool_fallback_to_chat_rate`。
3. 主题质量：`topic_fallback_ratio`、`new_topic_create_rate`。
4. 存储健康：分桶 DB 大小、写失败率、重试队列长度、`pending_sync` 数量。
5. 总结健康：计数触发次数、静默触发次数、LM v2 同步成功率。

### 8.2 测试门槛（MVP）

1. 单元测试：
- 工具优先与失败回退。
- 主题降级链路。
- 分桶路由稳定性。
- 混合触发器（20 条、60 分钟）。

2. 集成测试：
- 群聊与私聊端到端各一套。
- 图片缓存命中/未命中路径。
- LM v2 同步失败重试与恢复。

3. 回归测试：
- 非工具场景聊天质量不退化。
- 工具场景保持优先调用语义。
- 单点模型故障不导致“无回复”。

## 9. MVP 范围与非目标

### 9.1 本期必须交付

1. 文本+图片理解与图片描述永久缓存。
2. 工具优先路由和最小提示注入。
3. 单消息单主题归档。
4. `sqlite + sqlite-vec` 短期记忆分桶存储。
5. 混合触发总结并写入 LivingMemory v2。

### 9.2 本期不做

1. 视频解析（仅预留接口）。
2. 多主题归属和主题合并/拆分优化。
3. Web 管理后台。
4. 分布式任务队列。

## 10. 实施验收标准

1. 工具意图命中时，调用路径必须先尝试工具再降级聊天。
2. 任意消息都能稳定归档到单个主题。
3. 主题达到阈值或静默超时后能产出总结并进入 LM v2 同步流程。
4. 所有本地数据均落在 `/data/plugin_data/astrbot_plugin_chat_tool_balance`。
5. 短期记忆和图片缓存均使用 10 分桶 DB。

---

本设计文档为 MVP 基线，后续进入实现前将基于本稿生成分阶段执行计划。
