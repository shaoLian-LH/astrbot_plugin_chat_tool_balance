## 阶段目标

- 阶段编号：8
- 阶段名称：可观测性加固与发布闸门
- 目标说明：在全链路功能接入后完成日志指标加固、全量回归与灰度开关演练，输出可发布的执行结果。
- 对应总计划：`.slfk/docs/PLAN.md`

## 前置依赖

- 已完成阶段：`PHASE-1` 至 `PHASE-7`
- 外部输入：设计文档第 11、13、14 章
- 环境或权限要求：可执行全量测试；可修改 `services/`、`pipeline/`、`scheduler/` 与 `tests/`。

## 参考文档

- [设计基线（本地）](../../docs/plans/2026-03-10-responses-api-gateway-design.md)
- [openai-python（超时/重试行为）](https://github.com/openai/openai-python)
- [AstrBot AI 调用文档](https://docs.astrbot.app/dev/star/guides/ai.html)

## 任务清单

- [x] T01：补齐结构化日志字段
  - 变更目标：`services/llm_gateway/gateway.py`、`scheduler/summary_state_janitor.py`
  - 完成判定（definition_of_done）：关键路径日志包含 `role/provider_id/transport_used/fallback_reason_code/scope_id/topic_id/latency_ms/request_id`，失败路径可带 `error` 快速定位降级原因。
  - 验证证据：`tests/test_58_gateway_observability_metrics_success.py::test_gateway_records_responses_metrics_and_logs_for_chat_success`

- [x] T02：补齐指标埋点
  - 变更目标：`services/llm_gateway/observability.py`、`services/llm_gateway/gateway.py`、`scheduler/summary_state_janitor.py`、`services/runtime_wiring.py`
  - 完成判定（definition_of_done）：接入 `responses_attempt_total`、`responses_success_total`、`responses_fallback_total`、`responses_latency_ms_bucket`、`response_state_hit_total`、`response_state_cleanup_total`，并带 `role/reason` 维度。
  - 验证证据：`tests/test_58_gateway_observability_metrics_success.py`（3 个用例全部通过）

- [x] T03：执行回归测试矩阵（分层）
  - 变更目标：运行阶段定义的三条回归命令
  - 完成判定（definition_of_done）：命令全部通过，无阻断级失败
  - 验证证据：
    - `.venv/bin/pytest -q tests/test_44_gateway_config_contract_success.py tests/test_46_response_state_repository_success.py tests/test_48_provider_resolver_and_capability_router_success.py tests/test_50_gateway_transports_success.py tests/test_52_llm_gateway_sync_contract_success.py` -> `33 passed`
    - `.venv/bin/pytest -q tests/test_54_orchestrator_gateway_chat_flow_success.py tests/test_56_non_chat_roles_stateless_and_summary_cleanup_success.py` -> `4 passed`
    - `.venv/bin/pytest -q` -> `81 passed`

- [x] T04：执行灰度开关演练与回滚演练
  - 变更目标：同一测试环境验证 `features.use_responses_api=true/false` 双分支
  - 完成判定（definition_of_done）：开启开关时优先 Responses；关闭开关时稳定回退 AstrBotTransport
  - 验证证据：`.venv/bin/pytest -q tests/test_54_orchestrator_gateway_chat_flow_success.py::test_runtime_wiring_inject_gateway_and_chat_flow_success tests/test_54_orchestrator_gateway_chat_flow_success.py::test_runtime_wiring_feature_on_prefers_responses_transport_success` -> `2 passed`

- [x] T05：发布闸门判定
  - 变更目标：形成明确 go/no-go 结论与回滚动作
  - 完成判定（definition_of_done）：全量测试通过 + 开关演练通过 + fallback 失败率可控
  - 验证证据：
    - 结论：`GO`（当前测试矩阵与双开关演练均通过）
    - fallback 失败率：演练与回归中未出现 `E_FALLBACK_FAILED`（可视作 0）
    - 回滚动作：`features.use_responses_api=false`，全链路回退 `AstrBotTransport`

## 验证与验收

- 验证动作：执行任务 3测试命令与任务 4两轮演练。
- 验收标准：
  - 关键日志字段与指标可观测。
  - 开关开/关均能稳定运行。
  - 发布结论具有可执行回滚路径。
- 交付物检查（非文档类）：
  - 代码中的日志与指标埋点改动。
  - 测试通过的命令输出（运行产物）。
  - 开关演练产生的运行日志。

## 风险与回滚

- 风险：最终集成后出现低频异常（如 provider 间歇性 5xx）导致线上抖动。
  - impact：部分请求响应时间上升或短时失败。
  - mitigation：保持能力缓存与自动 fallback，监控 `responses_fallback_total{reason}` 快速告警。
  - rollback：立即将 `features.use_responses_api` 设为 `false`，全链路回退 AstrBotTransport。
