# 待办

## v1.4.8 ~ v1.5.1 (完成)

- [x] 固件获取修复（product_name fallback, update_firmware_info KeyError）
- [x] 双喷嘴支持（id=0 右/ id=1 左互通）
- [x] 剩余时间单位修复（双喷嘴机型用分钟）
- [x] 增量合并缺失字段（firmware_version, is_dual_nozzle, nozzle_temper_left, nozzle_target_left）
- [x] 维护任务程序化投种 (_seed_defaults)
- [x] 风扇百分比 / HMS 格式化 / 层进度 / AMS 湿度百分比 / 辅助挤出机 / 摄像头 / 耗材缓冲器 / 运动精度校准
- [x] `push.mode` 新增 `"native+log"` — 发送后注入对话上下文
- [x] `_inject_to_conversation(umo, user_text, assistant_text)` 方法
- [x] AI Push 人格注入 + 对话上下文注入
- [x] debug_log 默认关闭
- [x] 切刀间隔 80h→250h（PETG 实测）

## v1.6.0 (计划)

### 日历时间计数器
- 新增 `wall_start` 时间戳（插件首次初始化设置，持久化到 `bambu_state.json`）
- `calendar_hours = (now - wall_start) / 3600`，每次 `_evaluate` 更新
- 维护任务 `type` options 追加 `"calendar"`，支持日历周期（如每 336h = 14 天）
- 运动精度校准改为 `type: calendar, interval: 336`（不受打印频率影响）
- `/bambu counters` 输出追加 `calendar_hours`

### 当地湿度提醒
- WebUI 新增 `humidity_warning` 配置组：
  - `enabled` (bool, default false)
  - `threshold` (int, default 70%)
  - `location` (string, 城市名)
- 打印开始（IDLE/PREPARE → RUNNING）时查询湿度，超阈值推送警告
- `/bambu weather` 命令查询当前当地湿度及打印建议
- 结果缓存 30 分钟

### 米家本地数据源联动
- 通过局域网米家网关获取室内温湿度传感器数据
- 作为 `humidity_warning` 的数据源（替代公网 API）

### 活跃耗材显示
- 从 `extruder.info[].snow` 字段解析当前活跃料槽（AMS 编号 + 槽位号）
- `/bambu info` 追加当前耗材信息（类型/颜色/余量）

### 维护任务确认完成
- 跨过间隔阈值后**不自动确认**，进入 `pending_maintenance` 集合
- 用户未确认的维护项在**每次打印完成时持续提醒**
- `/bambu maintenance done <名称>`：确认完成，记录时间戳 + 计数值到 `maintenance_completed`，从 pending 移除
- `/bambu maintenance` 输出区分：未确认项 / 已完成项 / 下次触发时间
- 不同于 `skip`：`skip` 提前重置基准（我还没做，但暂时跳过），`done` 记录完成事实

### AI 管理工具
注册 6 个 FunctionTool，让 AI 在对话中代操配置：
- `bambu_set_alert` — 开关内置提醒类型
- `bambu_set_mute` — 设置静默时段
- `bambu_set_push_mode` — 切换推送模式
- `bambu_set_maintenance` — 修改维护任务间隔/启停
- `bambu_set_counter` — 手动设置计数器
- `bambu_add_rule` — 添加自定义提醒规则

## v1.7.0 (远期)

### 打印机远程控制
通过 MQTT 发布命令到 `device/{serial}/request`：

**AI FunctionTool 层** — 6 个控制工具供 AI 对话调用：
- `bambu_set_bed_temp` — 设置热床温度（烘干/保温）
- `bambu_set_nozzle_temp` — 设置喷嘴温度（预加热/换料）
- `bambu_pause_print` — 暂停当前打印
- `bambu_resume_print` — 恢复当前打印
- `bambu_stop_print` — 停止当前打印（需安全确认）
- `bambu_set_light` — 控制灯光（检查模型）

**典型场景**：
- "热床保持 70°C 5 分钟烘干打印板"
- "帮我把喷嘴加热到 250°C 准备换料"
- "暂停打印，我要检查一下"

**实现路径**：
- 层 1: AI 理解意图 → 调用对应 Tool
- 层 2: Tool → 翻译为 MQTT JSON payload 发布到 `device/{serial}/request`
- 层 3: 危险操作（停止打印）要求二次确认
