import json
from typing import Optional

import aiohttp

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

TIMEOUT = aiohttp.ClientTimeout(total=20)


def api_base(region: str) -> str:
    return "https://api.bambulab.com" if region == "global" else "https://api.bambulab.cn"


def origin(region: str) -> str:
    return "https://bambulab.com" if region == "global" else "https://bambulab.cn"


def accept_language(region: str) -> str:
    return "en-US,en;q=0.9" if region == "global" else "zh-CN,zh;q=0.9,en;q=0.8"


async def cloud_request(
    region: str,
    method: str,
    pathname: str,
    body: Optional[dict] = None,
    token: Optional[str] = None,
) -> dict:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": origin(region),
        "Referer": f"{origin(region)}/",
        "User-Agent": USER_AGENT,
        "Accept-Language": accept_language(region),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"{api_base(region)}{pathname}"

    result = {
        "status": 0,
        "headers": {},
        "json": None,
        "text": "",
    }

    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            if method.upper() == "GET":
                async with session.get(url, headers=headers, ssl=False) as resp:
                    result["status"] = resp.status
                    result["headers"] = dict(resp.headers)
                    result["text"] = await resp.text()
            else:
                async with session.post(url, headers=headers, json=body, ssl=False) as resp:
                    result["status"] = resp.status
                    result["headers"] = dict(resp.headers)
                    result["text"] = await resp.text()

        if result["text"]:
            try:
                result["json"] = json.loads(result["text"])
            except json.JSONDecodeError:
                pass
    except Exception as e:
        result["status"] = -1
        result["text"] = str(e)

    return result


def cloud_ok(result: dict) -> bool:
    if not (200 <= result.get("status", 0) <= 299):
        return False
    j = result.get("json")
    if j is None:
        return False
    for key in ("code", "errorCode", "statusCode"):
        val = j.get(key)
        if val is not None and val not in (0, "0", 200, "200", "SUCCESS"):
            return False
    return True


def cloud_error(result: dict) -> str:
    j = result.get("json") or {}
    err = j.get("error")
    if err:
        return str(err)
    for key in ("message", "msg", "error"):
        val = j.get(key)
        if val:
            return str(val)
    if result.get("text"):
        try:
            j2 = json.loads(result["text"])
            if isinstance(j2, dict):
                for key in ("message", "msg", "error"):
                    val = j2.get(key)
                    if val:
                        return str(val)
        except (json.JSONDecodeError, TypeError):
            pass
    return f"HTTP {result.get('status', 'unknown')}"


def find_deep(value, keys: list) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, dict):
        for k in keys:
            v = value.get(k)
            if v is not None and v != "":
                return str(v)
        for v in value.values():
            found = find_deep(v, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_deep(item, keys)
            if found:
                return found
    return None


def pick_token(json_data, headers: dict) -> str:
    token = find_deep(json_data, ["accessToken", "access_token", "token", "idToken"])
    if token:
        return token
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:]
    return ""


async def send_code(region: str, account: str) -> dict:
    is_email = "@" in account
    if is_email:
        pathname = "/v1/user-service/user/sendemail/code"
        payloads = [
            {"email": account, "type": "codeLogin"},
            {"account": account, "type": "codeLogin"},
            {"email": account},
        ]
    else:
        pathname = "/v1/user-service/user/sendsmscode"
        payloads = [
            {"phone": account, "type": "codeLogin"},
            {"account": account, "type": "codeLogin"},
            {"phone": account},
            {"mobile": account},
        ]

    for payload in payloads:
        result = await cloud_request(region, "POST", pathname, payload)
        if cloud_ok(result):
            return {
                "ok": True,
                "status": result["status"],
                "message": "验证码已发送，请查看手机或邮箱",
            }

    return {
        "ok": False,
        "status": result["status"],
        "message": f"发送验证码失败: {cloud_error(result)}",
    }


async def login(region: str, account: str, code: str) -> dict:
    pathname = "/v1/user-service/user/login"
    is_email = "@" in account
    if is_email:
        payloads = [
            {"email": account, "code": code, "type": "codeLogin"},
            {"account": account, "code": code, "type": "codeLogin"},
            {"account": account, "code": code},
        ]
    else:
        payloads = [
            {"phone": account, "code": code, "type": "codeLogin"},
            {"account": account, "code": code, "type": "codeLogin"},
            {"phone": account, "code": code},
            {"mobile": account, "code": code},
            {"account": account, "code": code},
        ]

    for payload in payloads:
        result = await cloud_request(region, "POST", pathname, payload)
        if cloud_ok(result):
            token = pick_token(result.get("json") or {}, result.get("headers", {}))
            if token:
                return {
                    "ok": True,
                    "status": result["status"],
                    "token": token,
                }

    return {
        "ok": False,
        "status": result["status"],
        "message": f"登录失败: {cloud_error(result)}",
    }


async def fetch_mqtt_username(region: str, token: str) -> dict:
    paths = [
        "/v1/design-user-service/my/preference",
        "/v1/user-service/my/profile",
        "/v1/user-service/user/info",
        "/v1/user-service/my/user",
    ]

    for pathname in paths:
        result = await cloud_request(region, "GET", pathname, token=token)
        if cloud_ok(result) and result.get("json"):
            uid = find_deep(result["json"], ["uid", "userId", "user_id", "id"])
            if uid:
                username = uid if uid.startswith("u_") else f"u_{uid}"
                return {"ok": True, "username": username}

    return {"ok": False, "message": f"获取 MQTT 用户名失败: {cloud_error(result)}"}


async def fetch_bindings(region: str, token: str) -> dict:
    result = await cloud_request(
        region, "GET", "/v1/iot-service/api/user/bind", token=token
    )
    if not cloud_ok(result):
        return {"ok": False, "message": f"获取打印机列表失败: {cloud_error(result)}"}

    devices = result.get("json", {}).get("devices", [])
    printers = []
    for d in devices:
        printers.append({
            "serial": d.get("dev_id", ""),
            "name": d.get("name", ""),
            "model": d.get("dev_product_name", ""),
            "online": d.get("online", False),
        })
    return {"ok": True, "printers": printers}
