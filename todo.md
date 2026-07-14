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

### 已知问题
- `debug_log` 开关无效：子模块改用 `from astrbot.api import logger` 后不受 `logging.getLogger().setLevel()` 控制。v1.6.0 重新设计日志架构时一并修复。

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
