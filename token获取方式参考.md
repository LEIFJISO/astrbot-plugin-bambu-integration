以下是拓竹登录 token 获取流程的完整技术参考，可直接供其他 AI 编程时使用。

---

## 1. 架构概览

文件：`后端配置工具/server.js`
运行时：Node.js（原生 `https` 模块，无第三方 HTTP 库）
存储：本地 JSON 文件 `data/config.json`
区域：`cn`（中国 `api.bambulab.cn`）和 `global`（国际 `api.bambulab.com`）

---

## 2. 核心函数清单

### 2.1 `apiBase(region)` — 第575行
```js
function apiBase(region) {
  return region === "global" ? "https://api.bambulab.com" : "https://api.bambulab.cn";
}
```

### 2.2 `cloudRequest(region, method, pathname, body, token)` — 第598行
底层 HTTP 请求封装。签名：
| 参数 | 类型 | 说明 |
|------|------|------|
| `region` | `"cn"\|"global"` | 区域 |
| `method` | `"GET"\|"POST"` | HTTP 方法 |
| `pathname` | `string` | API 路径（如 `/v1/user-service/user/login`） |
| `body` | `object\|null` | 请求体 JSON |
| `token` | `string\|undefined` | 已有的 access_token（可选，用于已登录后的请求） |

返回值：`Promise<{ status: number, headers: object, json: object|null, text: string }>`

关键细节：
- 请求头固定设置：
  ```
  Accept: application/json
  Content-Type: application/json;charset=UTF-8
  Origin: https://bambulab.cn  (或 bambulab.com)
  Referer: https://bambulab.cn/
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ... Chrome/125 ...
  ```
- `Accept-Language`：cn 区域为 `zh-CN,zh;q=0.9,en;q=0.8`，global 为 `en-US,en;q=0.9`
- 若传了 `token`，添加 `Authorization: Bearer <token>`
- 超时时间 20 秒
- **不校验 SSL 证书**（代码未设置 `rejectUnauthorized: false`，用的是默认的 `https.request`）

### 2.3 `cloudOk(result)` — 第650行
判断云 API 返回是否成功：
- HTTP 状态码 200-299
- JSON 响应中 `code`/`errorCode`/`statusCode` 为 `0`/`"0"`/`200`/`"200"`/`"SUCCESS"` 或不存在

### 2.4 `cloudError(result)` — 第658行
提取云 API 错误信息，优先级：`result.error` → `result.json.message` → `result.json.msg` → `result.json.error` → JSON 原文 → HTTP 状态码

### 2.5 `findDeep(value, keys)` — 第631行
递归搜索 JSON 对象中第一个匹配指定 key 的非空字符串值。
```js
function findDeep(value, keys) { /* 深度优先，遍历所有键和子对象 */ }
```

### 2.6 `pickToken(json, headers)` — 第645行
从登录响应中提取 token，优先级：
```js
function pickToken(json, headers) {
  return findDeep(json, ["accessToken", "access_token", "token", "idToken"])
    || String(headers.authorization || headers.Authorization || "").replace(/^Bearer\s+/i, "");
}
```
即先搜 JSON body 中的 `accessToken` → `access_token` → `token` → `idToken`，若都没有，则从响应头的 `Authorization` 字段中剥离 `Bearer ` 前缀作为 token。

---

## 3. 完整登录流程（两步）

### 流程总览

```
用户输入账号 → sendCode() → 用户收到验证码 → 用户输入验证码 → login() → 拿到 token
```

### Step 1: 发送验证码 — `sendCode(input)` 第665行

**入参：** `{ region, account }` （`account` 也可用 `phone` 或 `email` 字段传入）

**逻辑：**

```
if (account 含 "@")
  → 调用 POST {apiBase}/v1/user-service/user/sendemail/code
  → 依次尝试 payload:
    [ { email: account, type: "codeLogin" },
      { account, type: "codeLogin" },
      { email: account } ]
else
  → 调用 POST {apiBase}/v1/user-service/user/sendsmscode
  → 依次尝试 payload:
    [ { phone: account, type: "codeLogin" },
      { account, type: "codeLogin" },
      { phone: account },
      { mobile: account } ]
```

循环尝试直到成功（`cloudOk` 返回 true），全部失败则抛错。

**返回值：** `{ ok: true, status: <http_code>, message: "验证码已发送，请查看手机或邮箱" }`

---

### Step 2: 验证码登录拿 Token — `login(input)` 第688行

**入参：** `{ region, account, code }` （`code` 也可用 `verification_code`）

**逻辑：**

```
if (account 含 "@")
  → 调用 POST {apiBase}/v1/user-service/user/login
  → 依次尝试 payload:
    [ { email: account, code, type: "codeLogin" },
      { account, code, type: "codeLogin" },
      { account, code } ]
else
  → 调用 POST {apiBase}/v1/user-service/user/login
  → 依次尝试 payload:
    [ { phone: account, code, type: "codeLogin" },
      { account, code, type: "codeLogin" },
      { phone: account, code },
      { mobile: account, code },
      { account, code } ]
```

循环尝试直到成功。成功后：

1. 调用 `pickToken(result.json, result.headers)` 提取 token
2. 存入 `config.json` → `cloud.access_token`
3. 调用 `refreshMqttUsername()` 获取 MQTT 用户名
4. 调用 `fetchBindings()` 获取打印机绑定列表

**返回值：** `{ ok: true, status: <http_code>, token_present: true, mqtt_username: <boolean> }`

---

### Step 2.5: 获取 MQTT Username — `refreshMqttUsername()` 第722行

登录成功后自动调用。使用刚获取的 token 请求用户信息接口（依次尝试直到成功）：

| 顺序 | API 路径 | 方法 |
|------|----------|------|
| 1 | `/v1/design-user-service/my/preference` | GET |
| 2 | `/v1/user-service/my/profile` | GET |
| 3 | `/v1/user-service/user/info` | GET |
| 4 | `/v1/user-service/my/user` | GET |

从响应 JSON 中 `findDeep(json, ["uid", "userId", "user_id", "id"])` 提取 uid，若不以 `u_` 开头则添加此前缀，存入 `config.json` → `cloud.mqtt_username`。

---

### Step 2.6: 获取打印机绑定 — `fetchBindings()` 第781行

调用 `GET {apiBase}/v1/iot-service/api/user/bind`（带 `Authorization: Bearer <token>`）

返回的设备列表写入 `data/devices.json` 和 `data/device-history.jsonl`。

---

## 4. Token 的存储结构

`data/config.json` 中：
```json
{
  "cloud": {
    "region": "cn",
    "account": "用户手机号或邮箱",
    "access_token": "xxxxxxxxxx",
    "mqtt_username": "u_123456789",
    "token_expires_at": 0
  }
}
```

---

## 5. Token 的使用场景

| 场景 | 方式 | 位置 |
|------|------|------|
| 获取 MQTT username | `Authorization: Bearer <token>` 请求 `/v1/user-service/my/profile` 等 | 第722行 |
| 获取打印机列表 | `Authorization: Bearer <token>` 请求 `/v1/iot-service/api/user/bind` | 第781行 |
| 下发给 ESP 设备 | 作为 JSON payload 字段 `token` 通过 USB 串口或 HTTP GET 发送 | 第1508行 `pushConfigToEsp()` |
| 判断登录状态 | `Boolean(cfg.cloud.access_token)` | 第553行等多处 |

下发给 ESP 的完整 payload（第1518-1528行）：
```json
{
  "wifi_ssid": "...",
  "wifi_password": "...",
  "region": "cn",
  "mqtt_host": "cn.mqtt.bambulab.com",
  "mqtt_username": "u_123456789",
  "token": "access_token_value",
  "serial": "打印机序列号",
  "name": "打印机显示名",
  "brightness": 100
}
```

---

## 6. 对外 HTTP 路由

| 路由 | 方法 | 处理函数 | 说明 |
|------|------|----------|------|
| `/api/cloud/send-code` | POST | `sendCode(body)` | 发送验证码 |
| `/api/cloud/login` | POST | `login(body)` | 验证码登录 |
| `/api/cloud/bindings` | GET | `fetchBindings()` | 获取打印机列表 |

前端调用示例（第1665行）：
```js
await api("/api/cloud/login", {
  method: "POST",
  body: JSON.stringify({ region: "cn", account: "13800138000", code: "123456" })
});
```

---

## 7. 编程参考要点

1. **Payload 格式兼容是关键**：拓竹 API 对不同账号类型的 payload 格式要求不一致（如 `{phone, type}` vs `{account, type}` vs `{mobile}`），必须用多格式依次尝试的策略。
2. **Token 位置不固定**：登录响应中 token 可能在任何嵌套层级的 `accessToken`/`access_token`/`token`/`idToken` 字段中，也可能在 `Authorization` 响应头里，需要全面搜索。
3. **区域区分**：中国区与国际区的 API Base、Site Origin、Accept-Language、MQTT host 均不同。
4. **MQTT username = `u_` + uid**：用于后续 ESP 直连拓竹 MQTT broker (`cn.mqtt.bambulab.com:8883`)。
5. **Token 同时存在服务端和 ESP 端**：ESP 拿到 token 后会独立与云服务通信，不经过本 Node.js 服务。