# 推送系统待办

## v1.4.7 (当前)

- [x] 固件获取诊断日志（`update_firmware_info` INFO + `_handle_message` INFO）
- [x] `/bambu refresh` 输出请求结果
- [x] `debug_log` 默认开启

## v1.4.8 (计划)

### Native+Log 模式
- `push.mode` options 新增 `"native+log"`
- Native 推送发送后注入对话上下文：
  - user: `"[打印机通知]"`
  - assistant: `"{通知正文}"`
- 使后续 LLM 对话能"读到"已发送的通知

### AI Push 人格注入
- `_on_ai_push` 调用 `llm_generate` 前从 `conversation_manager` 获取当前会话的 `persona_id`
- 读取 `persona.system_prompt` 注入 prompt 首部
- AI Push 完成后也注入对话上下文

## v1.5.0 (远期)

### 完整 Agent 模式
- 研究如何在不持有 `AstrMessageEvent` 的后台场景中构造 Agent 上下文
- 让 AI 推送时可以调用 `bambu_printer_status` 等工具
