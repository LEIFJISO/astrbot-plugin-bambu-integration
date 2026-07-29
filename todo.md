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

## v1.5.5 (计划)

### 已完成
- [x] `/bambu status` 版本号硬编码 → 读取 `metadata.yaml`（`main.py:33,63,601`）
- [x] `debug_log` 开关修复 → 对 `logger.name` 真实名称 setLevel + handler 传播（`main.py:84-91`）
- [x] 补齐 `alert_engine.py` 的 `logger.debug()`（`_evaluate`/`_update_counters`/`_evaluate_maintenance`）
- [x] `native+log` 一行修复 → `"native+log"` 加入 dispatch 分支（`alert_engine.py:519`）
- [x] 删除 `dispatch()` 中重复的 config 读取（`alert_engine.py:505-516`）
- [x] 3 处 `except Exception: pass` → `logger.warning()`（`main.py` ×2 + `mqtt_client.py` ×1）
- [x] `_conf_schema.json` hint 补全 `native+log` 说明

### 待修复：增量 pushall 字段缺失（基于采集器数据）

**背景**：X2D 的 msg=1 增量 pushall 仅包含少量变化字段，大量关键字段缺失。
采集器 2h 数据证实：

| 字段 | msg=0（全量） | msg=1（增量） | 影响 |
|------|--------------|--------------|------|
| `bed_temper` | ✅ 有值 | ❌ 缺失 → default=0 | 降温/升温阶段误报 0°C，触发了 171 条 anomaly |
| `gcode_state` | ✅ 有值 | ❌ 缺失 → `""` | 每条约 1-3 次误判状态转换（空 vs 真值） |
| `nozzle_temper` | ✅ 有值 | ⚠️ 间歇有值 | 频繁跳到 0°C |
| `hms` | ✅ 数组 | ❌ `[]` | 每 20 秒 HMS 出现/消失循环 |

#### 3a. 床温修复 — `printer_manager.py`

**文件**：`printer_manager.py` 的 `update_from_pushall()`（第 223-325 行）

**问题**：增量路径先走 `_parse_temperature_standard(data)` → 字段缺失返回 0 → 后续 308-310 行的保留检查 `"bed_temper" not in data` 本来能兜底，但 parse 已经给了 default=0，新 state 的 `bed_temper` 已经是 0，检查通过（key 确实不在 data 中）→ 覆盖为 old 值。**但在此之前 `new_state` 对象已创建且 bed_temper=0**——如果中间有任何使用 `new_state` 的代码，拿到的就是 0。

**修复**：
```python
# 在温度解析前增加增量守卫
if is_incremental and old_state:
    if "bed_temper" not in data and "bed_target_temper" not in data:
        bed_current = old_state.bed_temper
        bed_target = old_state.bed_target_temper
        # 跳过 _parse_temperature_*
```

同时简化 308-310 行的冗余代码（守卫已在上面做了）。

#### 3b. gcode_state 修复 — `printer_manager.py`

**问题**：`new_state = PrinterState(gcode_state=str(data.get("gcode_state", "")))` — 增量时 key 缺失 → `""` → 与 old 的 `"RUNNING"` 不同 → 每次 delta 判定为状态变化 → `_on_state_change` 回调被频繁误触发。

**修复**：
```python
# 在 PrinterState 构造前
gcode_state = data.get("gcode_state")
if is_incremental and old_state and not gcode_state:
    gcode_state = old_state.gcode_state
else:
    gcode_state = str(gcode_state or "")
```

#### 3c. 喷嘴温度修复 — 同 3a

喷嘴温度的行为同床温，修复一并覆盖。

#### 3d. Cooldown 守卫 — `alert_engine.py`

**文件**：`alert_engine.py` 的 `_evaluate_cooldown()`（第 189 行）

**修复**：一行守卫，防止 `bed_temper=0` 误触发降温完成通知：
```python
if new.bed_temper <= 0:
    return events
```

### 待重做：HMS 去重策略（基于采集器数据）

**背景**：已实现的"FINISH/IDLE 清空"策略无法处理以下真实场景：
- X2D 上存在 2 个已排除的 HMS 码（助手/BambuStudio 不显示），但 **msg=0 全量推送永远携带它们**（固件残留）
- msg=1 增量推送的 `hms=[]`，每 20 秒一次"出现(msg=0)/消失(msg=1)"循环
- 两个码的 `ts_boot` 时间戳为 6 月 23/24 日——至少残留一个月

#### 4a. HMS 去重重新设计 — `alert_engine.py`

**新策略**：区分 msg=0 和 msg=1 的 HMS 行为

```
_hms_alerted[serial] = set of (attr, code)  ← 全局永久追踪，不清空依赖打印周期

规则:
1. 仅 msg=0 时检查 HMS（忽略 msg=1 的不可靠数据）
2. 新码 (不在 alerted) → 提醒一次，加入 alerted
3. 旧码仍在 alerted 且 msg=0 包含 → 静默
4. 旧码在 alerted 但 msg=0 不再包含 → 从 alerted 移除（固件真正清除了）
   下次再出现视为新事件
```

**需修改位置**：
- `alert_engine.py:210` — `_hms_alerted` 初始化
- `alert_engine.py:72-97` — `_build_state_summary()` 增加 `msg` 参数，仅 msg=0 时参与 HMS 判断
- `alert_engine.py:247+` — `_evaluate()` 中传递 `msg` 给 `_build_state_summary`
- `alert_engine.py:320+` — 移除 FINISH/IDLE 清空 `_hms_alerted` 的逻辑
- `alert_engine.py:245-250` — 新增 `load_hms_alerted()` / `get_hms_alerted()` 持久化方法（保持不变）
- `main.py:103-116` — 持久化逻辑保持不变

#### 4b. HMS 24h 冷却机制 — 可选增强

对于不想完全静默跨打印残留码的用户，增加冷却配置：
- 同一 HMS 码 24 小时内最多提醒 1 次
- WebUI 配置 `monitor.hms_cooldown_hours`（int, default 0 = 永久去重）

---

## v1.5.6 (计划) — AI Push 修复

主题：修复 AI 推送的人格注入和对话上下文。基于参考插件 `astrbot_plugin_proactive_chat` 的正确模式。

### 背景

当前 `_on_ai_push`（`main.py:269-330`）无法正常工作，根因分析确认 6 个问题：

### await 缺失（2 处）— 人格永不生效

**文件**：`main.py:301,306`

```python
# 改前（错误）：返回 coroutine 对象，不是实际值
persona = persona_mgr.get_persona(conv.persona_id)
default_v3 = self.context.persona_manager.get_default_persona_v3(umo)

# 改后（正确）：
persona = await persona_mgr.get_persona(conv.persona_id)
default_v3 = await self.context.persona_manager.get_default_persona_v3(umo)
```

**影响**：AI 推送时 `system_prompt` 永远为空字符串，人格从未注入。

### system_prompt 传递方式错误

**文件**：`main.py:312,320`

```python
# 改前（错误）：system_prompt 拼接到 prompt 字符串里
prompt = f"{system_prompt}\n\n{event_prompt}"
llm_resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)

# 改后（正确）：system_prompt 作为独立参数传递
llm_resp = await self.context.llm_generate(
    chat_provider_id=provider_id,
    prompt=event_prompt,
    system_prompt=system_prompt,
    contexts=history_messages,
)
```

**影响**：人格被当作用户输入处理，AI 会误解。独立传递让 AstrBot 框架正确注入人格。

### 对话上下文完全缺失

**文件**：`main.py`，在 LLM 调用前新增

```python
# 获取对话历史，让 AI 知道之前说了什么
conv_mgr = self.context.conversation_manager
cid = await conv_mgr.get_curr_conversation_id(umo)
history_messages = []
if cid:
    conv = await conv_mgr.get_conversation(umo, cid)
    if conv and conv.messages:
        for msg in conv.messages[-10:]:  # 最近 10 条
            history_messages.append(str(msg))
```

**影响**：AI 不知道前置对话，每次推送都像"首次说话"，HMS 提醒无法被 AI 感知到"自己已经提醒过了"。

### API 签名确认

**文件**：`main.py:314`

当前使用 `get_current_chat_provider_id(umo=umo)`，参考插件使用位置参数 `session_id`。需在修复前确认 AstrBot 框架的实际方法签名（可能是 `session_id` 而非 `umo`），否则该行可能返回 `None` 导致直接降级 native。

### 错误处理增强

**文件**：`main.py:326`

```python
# 改前：只 catch 外层
except Exception as e:
    logger.warning(f"AI 推送失败，降级到原生推送: {e}")
    await self._send_to_session(...)

# 改后：LLM 生成失败时也在日志中记录详细原因
# 保持降级到 native 的行为不变（这是正确的兜底）
```

降级逻辑保持不变，但增加日志详细度以便排查。

### 目标流程

修复后的完整 AI Push 流程：

```
AlertEvent 触发
  → 获取 notify targets
  → 获取会话 ID (cid)
  → 获取对话历史 (contexts) ← 新增
  → 获取人格 (system_prompt) ← await 修复
  → llm_generate(prompt, system_prompt, contexts) ← 参数修正
  → 发送 AI 生成的提醒
  → inject_to_conversation (AI 记住自己说了什么)
  ↓ 失败则降级 native
```

---

## v1.5.7 (计划) — 日志静默 + 自定义提醒 once 模式

### 背景

1. v1.5.6 的 `LogManager.configure_logger` 仍无法控制子模块 DEBUG 日志输出（AstrBot 框架层强制兜底 DEBUG），决定直接注释掉对终端用户无用的高频推送日志
2. Cooldown 降温通知在温度 40°C 附近震荡时重复触发，现有的 `edge` 模式因条件短路（True→False→True）无法阻止

### 注释调试日志

3 个文件 9 处 `logger.debug()` → `# logger.debug()`，保留 `logger.info()` 和 `logger.warning()`。

| 文件 | 注释行 | 内容 |
|------|--------|------|
| `mqtt_client.py` | 154, 196 | MQTT enqueued / print msg |
| `printer_manager.py` | 234, 301, 318, 324 | State msg/merged/first init/callback |
| `alert_engine.py` | 276, 451, 489 | Evaluate / Counter / Maint DEBUG |

### 新增 `once` 触发模式

**动机**：现有 `edge` 模式在温度震荡时重复触发（条件 False→True→False→True）。需要一种"触发一次后锁定"的模式。

**三种模式对比**：

| 模式 | 行为 | 复位条件 | 适用场景 |
|------|------|----------|----------|
| `edge` | 每次 F→T 触发一次 | 条件回到 F 即复位 | 进度节点、瞬时事件 |
| `level` | 持续 T 时周期性触发 | 条件变 F 即停 | 温度/湿度超限保持提醒 |
| **`once`** | 首次 F→T 触发后**锁定** | 新打印开始（RUNNING 状态转换）复位 | 降温完成、打印失败、一次性通知 |

**实现**：

1. `alert_engine.py __init__`：新增 `self._rule_once_fired: dict[str, bool] = {}`
2. `_evaluate_custom_rules`：新增 `trigger_mode == "once"` 分支
   ```python
   if trigger_mode == "once":
       if self._rule_once_fired.get(key, False):
           continue  # 已锁定
       if not prev and result:  # 首次上升沿
           cooldown = rule.get("cooldown", 0)
           if time.time() - self._custom_last_trigger.get(key, 0) < cooldown:
               continue
           self._custom_last_trigger[key] = time.time()
           self._rule_once_fired[key] = True
   ```
3. `_evaluate()` 中检测 RUNNING 转换 → `self._rule_once_fired.clear()`
4. `_conf_schema.json`：自定义规则模板 `trigger_mode` options 追加 `"once"`
5. Cooldown 降温通知改用 `_rule_once_fired[f"cooldown:{serial}"]` 去重

## v1.6.0 (计划) — 维护体系核心

主题：维护任务的触发与确认闭环。五项功能内聚于同一核心。

### 日历时间计数器
- 新增 `wall_start` 时间戳（插件首次初始化设置，持久化到 `bambu_state.json`）
- `calendar_hours = (now - wall_start) / 3600`，每次 `_evaluate` 更新
- 维护任务 `type` options 追加 `"calendar"`，支持日历周期（如每 336h = 14 天）
- 运动精度校准改为 `type: calendar, interval: 336`（不受打印频率影响）
- `/bambu counters` 输出追加 `calendar_hours`

### 活跃耗材显示
- 从 `extruder.info[].snow` 字段解析当前活跃料槽（AMS 编号 + 槽位号）
- `/bambu info` 追加当前耗材信息（类型/颜色/余量）

### 维护任务确认完成
- 跨过间隔阈值后**不自动确认**，进入 `pending_maintenance` 集合
- 用户未确认的维护项在**每次打印完成时持续提醒**
- `/bambu maintenance done <名称>`：确认完成，记录时间戳 + 计数值到 `maintenance_completed`，从 pending 移除
- `/bambu maintenance` 输出区分：未确认项 / 已完成项 / 下次触发时间
- 不同于 `skip`：`skip` 提前重置基准（我还没做，但暂时跳过），`done` 记录完成事实

### 维护重置命令
提供 `/bambu maintenance reset` 命令，覆盖两种场景：

**A. 维护标记完成（从此刻计时）**：用户刚做完全套维护，想让所有任务的基准从当前时刻重新算起。
- 将所有 `_maintenance_trigger[task_id]` 设为当前计数器值 `current`
- 下次触发 = `current + interval`（不再触发 overdue 提醒）
- `/bambu maintenance reset`（默认行为）

**B. 全部重置为未维护**：撤销所有自动重置，让所有 overdue 任务重新进入未确认状态。
- 将所有 `_maintenance_trigger[task_id]` 重置为 `0` 或删除
- 所有 `interval < current` 的任务立即进入 `pending`，下次评估时触发
- `/bambu maintenance reset --force`

同时提供 WebUI 配置页按钮（`_conf_schema.json` 中新增 `maintenance_reset` action 按钮）。

### 可扩展条件系统
将单一 `{type, interval}` 升级为可组合的条件列表：

**单条件（向后兼容）**：
```json
{"type": "hours", "interval": 250}
```

**多条件 OR**（运动精度校准）：
```json
{
  "combine_mode": "OR",
  "conditions": [
    {"type": "calendar", "interval": 336},
    {"type": "hours", "interval": 50}
  ]
}
```

**多条件 AND**（湿度 < 40% 且 打印超 50h）：
```json
{
  "combine_mode": "AND",
  "conditions": [
    {"type": "humidity", "operator": "<", "value": 40},
    {"type": "hours", "interval": 50}
  ]
}
```

**条件类型注册表**（新增类型只需加一行）：
```python
CONDITION_FIELDS = {
    "hours":       {"counter": "print_hours",      "label": "打印小时",  "is_interval": True},
    "completions": {"counter": "completion_count",  "label": "完成次数",  "is_interval": True},
    "calendar":    {"counter": "calendar_hours",    "label": "日历时间",  "is_interval": True},
    "filament_used": {"counter": "filament_used",  "label": "耗材用量(g)", "is_interval": True},
    "humidity":    {"counter": "current_humidity",  "label": "环境湿度",  "is_interval": False},
}
```

**评估逻辑**：
```python
def _evaluate_maintenance(self, serial):
    for task in tasks:
        conds = task.get("conditions") or [{"type": task["type"], "interval": task["interval"]}]
        mode = task.get("combine_mode", "OR")
        results = [self._check_condition(c, task_id) for c in conds]
        met = any(results) if mode == "OR" else all(results)
        if met: trigger(task)
```

### 耗材用量追踪 (filament_used 计数器)

**背景**：切刀等部件的维护周期官方按「打印卷数」推荐（常规 8-12 卷，高磨损 6-10 卷）。X2D pushall 不直接含 `print_weight` 字段，需通过 AMS 槽位 `remain` 变化推算。**定义 1 卷 = 1kg 净重。**

**累加逻辑**（`_update_filament` 方法，打印完成时触发）：

1. 打印前后对比每个 AMS 槽位 `remain` 百分比差值
2. 正常消耗：`delta = tray_weight × (old_remain - new_remain) / 100`（克）
3. 手动续盘检测：`new - old > 50`（跳变）→ 视作整盘用完 → `tray_weight` 全量计入
4. AMS 自动换料：追踪打印期间活跃槽位列表（`snow` 变化），各槽 delta 累加

**高磨损耗材加权**：

- 配置项 `monitor.abrasive_multiplier`（float, default `1.3`，WebUI 可调）
- 碳纤/玻纤/夜光/大理石/金属/木填充等耗材 → `filament_used += delta × multiplier`
- 识别来源：RFID `tray_info_idx` 硬编码表 + 材料名关键词兜底

**磨蚀性耗材 RFID 代码表**（来自 ha-bambulab + Bambu 官方）：

| 类别 | 代码 |
|---|---|
| 碳纤维 (PA/PET/PLA/ABS/PPA/PPS) | GFA50, GFG50, GFB51, GFN03-06, GFT01-02, GFT98, GFL50, GFL52-55, GFL98, GFG98, GFN98, GFN97, GFP96, GFP98 |
| 玻璃纤维 (ABS/PA/PPA/PP) | GFB50, GFN08, GFN96, GFP95, GFL51 |
| 颗粒填充 (大理石/闪光/夜光/Aero/Galaxy) | GFA07, GFA08, GFA11, GFA12, GFA15 |
| 关键词兜底 | "CF","GF","Carbon Fiber","Glass Fiber","Marble","Sparkle","Glow","Wood","Metal","Aero","Galaxy" |

**切刀默认维护任务**（双层条件 OR）：
```json
{"combine_mode":"OR","conditions":[{"type":"filament_used","interval":8000},{"type":"hours","interval":250}]}
```
等效消耗 8kg 常规耗材（≈8 卷）**或**打印 250h → 先到先触发。

---

## v1.7.0 (计划) — 环境与安全

主题：物理安全与环境感知。五项功能聚焦于保护硬件和耗材。

### 热端兼容性检测
- 材料分类常量：ENGINEERING / HIGH_TEMP / LOW_TEMP（来自 Bambu 官方分类）
- 打印开始（IDLE→RUNNING）时检测当前耗材类型 vs 热端材质
- 工程材料（PA-CF/PPA-CF/PET-CF/PPS-CF/ABS-GF/ASA-CF/PC 等）→ 一律暂停并推送确认通知
  - 暂停命令：`{"print": {"command": "pause"}}` 发布到 `device/{serial}/request`
  - 通知含上次/当前热端类型，提示用户确认或更换
  - `/bambu confirm hotend` → 恢复打印 + 更新 `last_tray_type`/`last_nozzle_type`
- 高温→低温切换（ASA→PLA 等）→ 仅推送碳化警告，不暂停
- `per_nozzle` 状态持久化到 `bambu_state.json`（按喷嘴 id 分别追踪上次的 nozzle_type 和 tray_type）

**X2D 双喷嘴适配**（来源：Bambu 官方 X2D 耗材兼容性指南）：

X2D 的主喷嘴（右）与辅助喷嘴（左）有不同的兼容性等级：

| 材料 | 主喷嘴 | 辅助喷嘴 | 说明 |
|------|--------|----------|------|
| TPU / TPU for AMS / TPU 95A HF | ✅ | ⛔ 极不推荐 | X2D 仅主喷嘴可打 TPU，辅助喷嘴禁止 |
| PETG / PETG HF / PETG-CF | ✅ | ⚠️ 不推荐 | 辅助喷嘴质量差，建议仅用于支撑 |
| PLA Silk | ⚠️ 不推荐 | 需验证 | 所有喷嘴均不推荐 Silk，但可用 |
| PLA-CF / PLA-GF | ✅ (≥0.4mm) | ✅ (≥0.4mm) | 需硬化钢喷嘴 |
| ABS / ASA | ✅ | ✅ | 双喷嘴无限制 |
| PA/PC 工程材料 | ✅ | ✅ | 需硬化钢喷嘴 |

**耗材变轨器（Filament Track Switch）兼容性**：独立于喷嘴，某些耗材在变轨器中有卡料/刨料/断丝风险：
- ⛔ 极不推荐：TPU 全系列（刨料严重）、PLA-CF（断丝）
- ⚠️ 不推荐：Support for ABS（卡料）

检测逻辑需分层：
1. 识别当前活跃喷嘴（main vs auxiliary）
2. 检查该喷嘴的材料兼容性 → 严重不兼容则暂停
3. 检查变轨器兼容性 → 仅推送警告（不停机）

### MQTT 命令通道
- `mqtt_client.py` 新增 `pause_print(serial)` / `resume_print(serial)` / `stop_print(serial)`
- 为热端暂停恢复和后续 AI 远程控制提供基础命令通道

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

### 涂胶提醒
根据打印板类型与耗材类型的组合，在打印开始时推送涂胶提醒。

> **⚠️ 限制**：MQTT pushall 中**不含 `bed_type` 字段**。打印板类型仅存在于切片的 .3mf 文件元数据中（`Metadata/plate_N.json`），不是实时上报数据。
> 因此 v1.7.0 采用手动配置方案，自动检测延后至 v1.9.0。

**板子类型来源**（方案 D：手动配置 + 温度辅助）：
- **主途径**：WebUI 新增 `glue_reminder.plate_type` 配置项（string, options: `Cool Plate`/`SuperTack`/`SuperTack Pro`/`Engineering Plate`/`Smooth PEI`/`Textured PEI`），由用户手动设置当前使用的板子
- **辅助校验**：通过 `bed_target_temper` 做模糊判断——不同板子打印同耗材时有不同温度区间（如 PLA@Cool Plate 35-45°C vs PLA@Textured PEI 45-65°C），温度与配置不符时推送疑问提示（"检测到热床 40°C，请确认是否为冷打板？"）

**板子类型映射**（与 v1.9.0 自动检测共享）：
- `Cool Plate` — 低温打印板（已停产，仅 PLA，必须涂胶）
- `Cool Plate SuperTack` — 低温增稳板（PLA/PETG，无需涂胶，禁止 TPU）
- `Cool Plate SuperTack Pro` — 低温增稳板 Pro（PLA/PETG，无需涂胶）
- `Engineering Plate` — 工程打印板（任何耗材均推荐涂胶）
- `Smooth PEI / High Temp Plate` — 光面 PEI（除 PLA 外均需涂胶）
- `Textured PEI Plate` — 纹理 PEI（一般无需涂胶）

**涂胶规则矩阵**（来源：Bambu 官方耗材指南）：

| 板子 | PLA | PETG | ABS/ASA | TPU | PA/PC |
|------|-----|------|---------|-----|-------|
| Cool Plate (旧) | **必须** | 不推荐 | 不推荐 | 必须 | 不推荐 |
| SuperTack / Pro | 无需(禁Silk) | 无需 | — | **禁止** | — |
| Engineering | **推荐** | **推荐** | 推荐 | — | 推荐 |
| Smooth PEI | 无需 | **必须** | **必须** | — | **必须** |
| Textured PEI | 无需 | 无需 | 无需 | — | 无需 |

**触发逻辑**：
- 打印开始（IDLE→RUNNING）时，读取当前 `bed_type` + 活跃料槽耗材类型
- 匹配规则矩阵，按严重程度分级推送：
  - `required`（必须涂胶，不涂会损板）→ **警告级**推送
  - `recommended`（推荐涂胶，增强粘附/便于脱模）→ 普通提醒级推送
  - `forbidden`（禁止组合，如 TPU + SuperTack）→ **严重警告**，建议暂停
- 首次打印该组合时提醒一次（记录到 `bambu_state.json` 去重，板子更换后重置）

**配置项**（WebUI `glue_reminder` 组）：
- `enabled` (bool, default true)
- `remind_once_per_session` (bool, default true) — 每次连接 MQTT 后首次提醒一次

**X2D 特例规则**（来源：Bambu 官方 TPU 打印指南 + 打印板介绍）：

| 规则 | 通用 | X2D |
|------|------|-----|
| TPU + 纹理 PEI | 通常需涂胶 | **不涂胶**（官方：纹理 PEI 涂胶会导致 TPU 粘性过强，难以取下） |
| TPU + 冷打板 (SuperTack) | 禁止 | **不兼容** — TPU 需工程板/PEI 板 |
| 打印板识别 | 自动 | **一代板不被 X2D 识别** — 用户需手动选择"忽略"或关闭检测。检测到一代板 + X2D 时推送提示 |
| 工程板 | 任何耗材均推荐涂胶 | X2D 同样适用，打印前必涂 |

### 热端维护提醒（讨论）

**现状**：`nozzle_type` 已解析（`PrinterState.nozzle_type`，值如 `HS01`=硬化钢、`HH01`=碳化钨）。`device.nozzle.info[].wear` 字段存在但固件未活跃更新（275h 显示 0.0，占位数据）。

**待定考虑**：

**1. 热端更换生命周期**：
- 热端检查提醒 → 用户 `/bambu maintenance done 热端检查` → 记录更换时间戳 → 重置基准
- 更换硬件后 `nozzle_type` 自动变化（如 HS01→HH01），可读取但暂不自动调整间隔

**2. 基于 nozzle_type 的间隔自适应**：
- 硬化钢 HS01: 默认 500h
- 碳化钨 HH01: 1000h（约 2× 寿命）
- 不锈钢 SS01: 300h

**3. wear 字段的可靠使用**：
- 当前为占位值 0.0，需等待固件更新后才可依赖

**4. 默认热端检查任务**（待确认间隔后加入 `_seed_defaults`）：
| 名称 | 类型 | 间隔 | 说明 |
|---|---|---|---|
| 热端检查 | hours | 500 | 加热棒/热敏电阻/喉管积碳/喷嘴磨损 |

---

## v1.8.0 (计划) — AI 赋能

主题：AI 代操配置与远程控制。全部 FunctionTool，纯 AI 交互层。

### AI 管理工具
注册 6 个 FunctionTool，让 AI 在对话中代操配置：
- `bambu_set_alert` — 开关内置提醒类型
- `bambu_set_mute` — 设置静默时段
- `bambu_set_push_mode` — 切换推送模式
- `bambu_set_maintenance` — 修改维护任务间隔/启停
- `bambu_set_counter` — 手动设置计数器
- `bambu_add_rule` — 添加自定义提醒规则

### AI 远程控制打印机
基于 v1.7.0 的 MQTT 命令通道，注册 AI FunctionTool：
- `bambu_set_bed_temp` / `bambu_set_nozzle_temp` — 温度控制
- `bambu_pause_print` / `bambu_resume_print` / `bambu_stop_print` — 打印控制
- `bambu_set_light` — 灯光控制
- 危险操作（停止打印）需 AI 二次确认

### 耗材管理与使用追踪

**背景**：AMS 托盘数据（`tray_info_idx`、`tray_weight`、`remain`、`tray_type`、`tray_color`）在 msg=0 pushall 中完整可用（采集器已确认）。用户可以查询当前各槽位耗材状态、已用长度、预估剩余可打印量。

> **⚠️ 限制**：仅能追踪**拓竹官方耗材或安装 RFID 标签的第三方耗材**。AMS 通过 RFID 识别 `tag_uid` 和 `tray_info_idx`，无 RFID 标签的耗材（或使用外部料盘 `vt_tray`）只能拿到 `tray_type`（类型名），无法获取初始重量和材料详情。

**需考虑的问题**：

1. **无 RFID 耗材的识别**：外部料盘（`vt_tray`）或无标签 AMS 槽位，`tag_uid = "0000000000000000"`，`tray_weight = "0"`。插件应提供 WebUI 配置项让用户**手动录入耗材类型、颜色、初始重量**。录入后持久化到 `bambu_state.json`，按 `(serial, ams_id, tray_id)` 索引。

2. **耗材更换检测**：`tag_uid` 变化（≠旧值）→ 自动视为换盘 → 重置该槽位的 used 计数 → 载入新的 `tray_weight` → 推送通知（"检测到 AMS X 槽 Y 已更换耗材"）

3. **耗材消耗追踪**：
   - 已知 `remain` 是百分比（0-100），`tray_weight` 是初始重量（克）
   - `filament_used = tray_weight × (old_remain - new_remain) / 100`（克）
   - 累加到 `bambu_state.json` 的每个槽位计数器
   - `/bambu filament` 命令输出各槽位的当前状态

4. **耗材低量提醒**：
   - WebUI 配置 `filament_low_threshold`（%, default 10）
   - 当活跃槽位 `remain < threshold` 时推送提醒
   - 结合下一槽是否有同类型耗材（自动续打的 AMS 支持），提示用户是否需要提前补充

5. **跨槽位追踪**：同一耗材在不同槽位间移动时，`tag_uid` 跟随 → 已消耗量应跟随耗材本身而非槽位。按 `tag_uid` 索引耗材状态，而非 `(ams_id, tray_id)`。

6. **耗材库管理**：
   - `/bambu filament list` — 列出所有已知耗材（按 tag_uid 去重），含类型、颜色、累计消耗、当前所在槽位
   - `/bambu filament history` — 列出最近的耗材更换/消耗事件

7. **独立耗材管理 Webpage + 重量秤联动（可能单开项目）**：
   - 独立 Webpage（FastAPI + WebSocket）提供耗材库存可视化、手动录入界面、消耗趋势图
   - 方案 A（USB HID 秤 / 优先）：通用 USB 厨房秤/珠宝秤，`hidapi` 读取 HID 报告 → WebSocket 推送到前端实时显示
   - 方案 B（串口秤）和方案 C（蓝牙 BLE 秤）作为备选
   - 联动流程：用户放耗材盘到秤上 → Webpage 实时显示重量 → 选择"此盘属于 AMS 槽 X" → 插件记录初始重量
   - **若复杂度超出插件本身范围，考虑单开独立项目**（独立 repo、独立部署）

### 计划

| 功能 | 复杂度 | 优先级 |
|------|--------|--------|
| 耗材状态查询 `/bambu filament` | 中 | P0 |
| 耗材消耗自动追踪（remain 变化累加） | 中 | P0 |
| 无 RFID 耗材手动录入 | 低 | P1 |
| 耗材更换自动检测 + 通知 | 低 | P1 |
| 耗材低量提醒 | 低 | P1 |
| 跨槽位耗材追踪（按 tag_uid） | 中 | P2 |
| 耗材库历史 | 低 | P2 |
| 独立耗材管理 Webpage | 高 | P3（可能单开项目） |
| 重量秤联动（USB HID） | 高 | P3（依赖 Webpage，可能单开项目） |

---

## v1.9.0 (计划) — 高级自动化

主题：当前因技术限制无法实现、需要额外研发的高级自动化功能。

### AI 发起打印

**场景**：用户提前在打印机 SD 卡上存好 .3mf 文件（通过 FTP/FTPS 上传），但本人不在家。父母/室友只会帮忙取下完成件，不会操作打印机发起新任务。用户通过聊天命令让 AI 或插件代发打印。

**前提条件**：
- 3MF 文件已通过 FTPS（端口 990）上传到打印机 SD 卡
- 打印机处于 IDLE 状态（热床无残留模型）
- 用户通过聊天自然语言描述"帮我用第二块板打那个 PETG 模型" → AI 解析意图

**技术实现**：

1. **FTPS 文件上传**：
   - 通过 Python `ftplib_tls`（标准库）连接打印机 FTPS
   - 认证同 MQTT：`bblp:<access_code>`
   - 上传 .3mf 文件到打印机可识别的路径

2. **FTPS 文件浏览**：
   - 列出 SD 卡上可用文件
   - `/bambu files` 命令展示文件列表（文件名 + 上传时间 + 大小）
   - AI FunctionTool: `bambu_list_files` — 返回可用 3MF 文件供 AI 推荐

3. **打印发起命令**：
   - MQTT `print.project_file` 命令（参考 OpenBambuAPI 协议）
   - 需指定：`url`（ftp 路径）、`param`（plate_N.gcode）、`use_ams`、`ams_mapping`
   - 需支持指定使用哪块板（plate_1 / plate_2 / ...）
   ```json
   {
     "print": {
       "command": "project_file",
       "param": "Metadata/plate_1.gcode",
       "url": "ftp://model.gcode.3mf",
       "use_ams": true,
       "ams_mapping": [0, 1, -1, -1],
       "bed_leveling": true,
       "flow_cali": true,
       "vibration_cali": true,
       "timelapse": false,
       "bed_type": "auto"
     }
   }
   ```

4. **AI 交互流程**：
   ```
   用户: "帮我打印那个 PETG 支架，用 AMS 第二槽的料"
   AI: 调用 bambu_list_files → 看到 "PETG_Bracket.gcode.3mf"
       调用 bambu_status → 确认打印机 IDLE
       调用 bambu_ams_status → 确认 AMS 槽 2 有 PETG，remain=85%
       → "好的，我将使用 PETG_Bracket.gcode.3mf 的第一块板，
          AMS 槽 2 的 PETG Basic 打印。开始吗？"
   用户: "开始"
   AI: 调用 bambu_start_print("PETG_Bracket.gcode.3mf", plate=1, ams_slots=[2])
       → 打印已发起
   ```

5. **安全机制**：
   - **打印前必须确认**：AI 发起打印前需用户明确确认（不能单次对话直接执行）
   - **热床确认**：检查是否有前次打印残留模型未取下
   - **耗材匹配检查**：3MF 预设耗材 vs AMS 当前料槽是否匹配，不匹配时推送警告
   - **打印机状态检查**：IDLE 才允许发起
   - **签名问题**：X2D/H2D 的 `print.*` 需 RSA 签名。走 Bambu Cloud MQTT 通路（JWT 认证，服务端代签），原理应可用。需实际验证

6. **FunctionTool 设计**：
   - `bambu_list_files` — 返回 SD 卡文件列表
   - `bambu_start_print` — 发起打印（参数：文件名、板号、AMS 槽位映射）
   - `bambu_check_filament_match` — 检查 3MF 预设耗材 vs AMS 当前耗材是否匹配
   - `bambu_cancel_print_pending` — 取消等待确认的打印请求

6. **打印文件存储双路径**：

| 路径 | 存储位置 | 适用场景 | 技术方式 |
|------|----------|----------|----------|
| **A: 打印机 SD 卡** | 打印机内置存储 | 用户已通过 Bambu Studio/Handy 上传好文件 | FTPS 浏览 + 直接引用 FTP 路径发起打印 |
| **B: AI 服务器** | AstrBot 所在主机 | 用户通过聊天/Webpage 上传 .3mf，由插件代传到打印机 | HTTP 上传 → `data/print_files/` 暂存 → FTPS 推送至打印机 → 发起打印 |

路径 B 额外设计：
- Webpage 或聊天命令提供文件上传接口
- 通过 `aioftp` / `ftplib_tls` 将暂存文件推送到打印机 SD 卡
- 打印完成后可选保留/删除源文件
- `/bambu files` 命令列出服务器 + 打印机两边的文件

7. **限制与风险**：
   - 需有 MQTT 命令通道（v1.7.0）和 FTPS 连接能力
   - FTPS 连接可能被路由器防火墙或网络隔离阻断
   - 无法确认热床上是否真有残留模型（只能通过 `gcode_state == IDLE` 推断）

### 打印板类型自动检测
**背景**：`bed_type` 不在 MQTT pushall 实时上报中（v1.7.0 涂胶提醒依赖手动配置）。真正可靠的获取途径是解析打印文件的 3MF 元数据。

**方案**：
- 检测到打印开始（`IDLE→RUNNING` + 新 `gcode_file`）时，通过 FTPS（端口 990）连接打印机
- 下载 `.gcode.3mf` 文件，解压 zip 读取 `Metadata/plate_N.json` 中的 `bed_type` 字段
- 值映射：`"cool_plate"` / `"engineering_plate"` / `"smooth_pei_plate"` / `"textured_plate"` / `"hot_plate"`
- 缓存结果到 `bambu_state.json`，同一 `gcode_file` 不重复解析
- FTPS 认证复用云登录获取的 `access_code`（`bblp:<code>` 凭据）

**风险/复杂度**：
- 需要额外的 aioftp 或 ftplib 依赖
- 3MF 文件可能较大（包含几何数据），仅需下载 zip 的 central directory + 小的 JSON 文件（可优化为 range request）
- 打印文件通过 SD 卡或 USB 直接打印时无 3MF 可解析 → 降级为用户手动配置

### 夜间自动校准
**背景**：插件已有校准提醒（维护任务 #14 全面校准 500h、#15 运动精度校准 168h），但仅提醒不执行。
打印机固件支持 `print.calibration` MQTT 命令（bitmask: lidar=1, bed_level=2, vibration=4, motor_noise=8）。

**触发条件**：
- 打印机状态为 IDLE
- 当前时间在可配置夜间窗口（如 02:00-06:00）
- 距上次校准超过可配置间隔（如每 7 天）
- 用户 opt-in 开启此功能

**限制**：
- **X2D/H2D 签名问题**：`print.*` namespace 命令需 RSA-SHA256 签名（Jan 2025+ 授权控制固件）。插件走 Bambu Cloud MQTT 通道路由，理论上服务端代签。需实际验证。
- 校准前需确保打印板无残留模型、无 AMS 换料中状态
- 深夜噪音需用户明确知情同意

**WebUI 配置**（`auto_calibration` 组）：
- `enabled` (bool, default false)
- `night_window_start` / `night_window_end` (string, "02:00" / "06:00")
- `interval_days` (int, default 7)
- `calibration_items` (multi-select: lidar/bed_level/vibration/motor_noise)
