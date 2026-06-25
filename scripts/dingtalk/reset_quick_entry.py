#!/usr/bin/env python3
"""重置钉钉单聊机器人快捷入口。

用途：清空机器人当前全部快捷入口，然后注册**单个**「教练模式」入口——
点击后打开 cocah.html 页面（访前准备教练 / 访后复盘教练），图标用 coach_mode.png。

调用链路（anniu.md 验证通过）：
  AppKey/AppSecret → access_token
  → oapi/media/upload(coach_mode.png) → media_id
  → POST /v1.0/robot/plugins/clear          （清空全部）
  → POST /v1.0/robot/plugins/set            （全量覆盖，注册单个入口）

无需重建容器：脚本直接调用钉钉服务端 API，凭证从 secrets/taishan.env 读取。

用法：
  PYTHONPATH=src python scripts/dingtalk/reset_quick_entry.py --dry-run   # 只查不改
  PYTHONPATH=src python scripts/dingtalk/reset_quick_entry.py             # 清空 + 注册
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.parse
from pathlib import Path

import httpx
from dotenv import load_dotenv

# 让脚本能 import sales_agent.*（运行时通常配合 PYTHONPATH=src）
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from sales_agent.integrations.dingtalk.message_sender import DingTalkAccessTokenManager  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV = REPO_ROOT / "secrets" / "taishan.env"
DEFAULT_ICON = REPO_ROOT / "src" / "sales_agent" / "integrations" / "dingtalk" / "static" / "coach_mode.png"

# 要注册的快捷入口：(展示名, action)。action=None → 教练模式默认视频页(/t/{tenant}/quick)；
# 其余 → /t/{tenant}/quick?action=<action> （触发页）。
ENTRIES = [
    ("教练模式", None),
    ("小赢欣赏", "small_win_appreciation"),
    ("卡点破框", "sales_block_breakthrough"),
]

PLUGIN_SET_URL = "https://api.dingtalk.com/v1.0/robot/plugins/set"
PLUGIN_CLEAR_URL = "https://api.dingtalk.com/v1.0/robot/plugins/clear"
PLUGIN_QUERY_URL = "https://api.dingtalk.com/v1.0/robot/plugins/query"
MEDIA_UPLOAD_URL = "https://oapi.dingtalk.com/media/upload"


def _pc_sidebar_url(https_url: str) -> str:
    """把 https 入口 URL 包成 dingtalk:// 协议，强制 PC 端在钉钉侧边栏内嵌打开。

    背景：PC 钉钉点机器人快捷入口，若 pcUrl 是普通 https，会跳到系统浏览器打开——
    那里没有钉钉 JSAPI 桥（报 notInDingTalk / 5010），requestAuthCode 走不通。
    用 dingtalk://dingtalkclient/page/link?url=<编码后的 https> 尝试让钉钉在内嵌窗口打开。
    实验性，是否被钉钉接受需实测；回退：不带 --pc-sidebar 重跑即可恢复 https。
    """
    return "dingtalk://dingtalkclient/page/link?url=" + urllib.parse.quote(https_url, safe="")


def _load_env(env_file: Path) -> dict[str, str]:
    if not env_file.exists():
        sys.exit(f"[ERROR] env file not found: {env_file}")
    load_dotenv(env_file, override=True)
    import os

    keys = ["DINGTALK_APP_KEY", "DINGTALK_APP_SECRET", "DINGTALK_ROBOT_CODE",
            "DINGTALK_PUBLIC_URL", "TENANT_ID"]
    cfg = {k: os.environ.get(k, "") for k in keys}
    missing = [k for k in keys if not cfg[k]]
    if missing:
        sys.exit(f"[ERROR] missing env vars in {env_file}: {', '.join(missing)}")
    return cfg


async def _query(client: httpx.AsyncClient, token: str, robot_code: str) -> dict:
    resp = await client.post(
        PLUGIN_QUERY_URL,
        json={"robotCode": robot_code},
        headers={"x-acs-dingtalk-access-token": token},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"plugins/query failed: {resp.status_code} {resp.text[:300]}")
    return resp.json() if resp.text else {}


async def _clear(client: httpx.AsyncClient, token: str, robot_code: str) -> dict:
    resp = await client.post(
        PLUGIN_CLEAR_URL,
        json={"robotCode": robot_code},
        headers={"x-acs-dingtalk-access-token": token},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"plugins/clear failed: {resp.status_code} {resp.text[:300]}")
    return resp.json() if resp.text else {}


async def _set(client: httpx.AsyncClient, token: str, robot_code: str, plugin_info_list: list[dict]) -> dict:
    resp = await client.post(
        PLUGIN_SET_URL,
        json={"robotCode": robot_code, "pluginInfoList": plugin_info_list},
        headers={"x-acs-dingtalk-access-token": token},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"plugins/set failed: {resp.status_code} {resp.text[:300]}")
    return resp.json() if resp.text else {}


async def _upload_icon(client: httpx.AsyncClient, token: str, icon_path: Path) -> str:
    with open(icon_path, "rb") as f:
        resp = await client.post(
            MEDIA_UPLOAD_URL,
            params={"access_token": token, "type": "image"},
            files={"media": (icon_path.name, f, "image/png")},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"media/upload failed: {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    if data.get("errcode") not in (0, None):
        raise RuntimeError(f"media/upload error: {data}")
    media_id = data.get("media_id", "")
    if not media_id:
        raise RuntimeError(f"no media_id in upload response: {data}")
    return media_id


async def main(args: argparse.Namespace) -> int:
    cfg = _load_env(args.env_file)
    robot_code = cfg["DINGTALK_ROBOT_CODE"]
    public_url = cfg["DINGTALK_PUBLIC_URL"].rstrip("/")
    tenant_id = args.tenant_id or cfg["TENANT_ID"]
    icon_path = Path(args.icon)

    print(f"[INFO] robot_code = {robot_code}")
    print(f"[INFO] public_url = {public_url}")
    print(f"[INFO] tenant_id  = {tenant_id}")
    print(f"[INFO] name       = {args.name}")
    print(f"[INFO] icon       = {icon_path}")
    if not icon_path.exists():
        sys.exit(f"[ERROR] icon not found: {icon_path}")

    token_mgr = DingTalkAccessTokenManager(cfg["DINGTALK_APP_KEY"], cfg["DINGTALK_APP_SECRET"])
    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await token_mgr.get_access_token()
        print("[INFO] access_token acquired")

        # 1. 查询当前快捷入口
        before = await _query(client, token, robot_code)
        print("\n===== 当前快捷入口 (before) =====")
        print(json.dumps(before, ensure_ascii=False, indent=2))

        def _entry_url(zh_name: str, action: str | None) -> str:
            base = f"{public_url}/integrations/dingtalk/t/{tenant_id}/quick"
            return f"{base}?action={action}" if action else base

        print("\n[INFO] 计划注册的快捷入口：")
        if args.pc_sidebar:
            print("[INFO] ⚠️ --pc-sidebar 已开启：pcUrl 用 dingtalk:// 协议（强制 PC 侧边栏）；mobileUrl 保持 https")
        for zh_name, action in ENTRIES:
            u = _entry_url(zh_name, action)
            pc_u = _pc_sidebar_url(u) if args.pc_sidebar else u
            print(f"  - {zh_name}  ({action or '教练模式/默认页'})")
            print(f"      pcUrl    = {pc_u}")
            print(f"      mobileUrl= {u}")

        if args.dry_run:
            print("\n[DRY-RUN] 不会清空/注册。")
            return 0

        # 2. 上传图标（三个入口复用同一个 coach_mode.png）
        media_id = await _upload_icon(client, token, icon_path)
        print(f"\n[INFO] icon uploaded, media_id = {media_id}")

        # 3. 清空全部已有快捷入口（set 是追加，必须先 clear）
        cleared = await _clear(client, token, robot_code)
        print(f"[INFO] plugins/clear result = {json.dumps(cleared, ensure_ascii=False)}")

        # 4. 一次性注册全部入口
        plugin_list = []
        for zh_name, action in ENTRIES:
            url = _entry_url(zh_name, action)
            pc_url = _pc_sidebar_url(url) if args.pc_sidebar else url
            plugin_list.append({
                "name": json.dumps({"zh_CN": zh_name}, ensure_ascii=False),
                "icon": media_id,
                "pcUrl": pc_url,
                "mobileUrl": url,
            })
        result = await _set(client, token, robot_code, plugin_list)
        print(f"[INFO] plugins/set result = {json.dumps(result, ensure_ascii=False)}")

        # 5. 复查
        after = await _query(client, token, robot_code)
        print("\n===== 当前快捷入口 (after) =====")
        print(json.dumps(after, ensure_ascii=False, indent=2))

        print(f"\n[DONE] 已注册 {len(plugin_list)} 个快捷入口（教练模式 / 小赢欣赏 / 卡点破框），均指向 {public_url}")

    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="重置钉钉单聊机器人快捷入口（清空 + 注册单个教练模式入口）")
    p.add_argument("--env-file", type=Path, default=DEFAULT_ENV, help=f"凭证 env 文件（默认 {DEFAULT_ENV}）")
    p.add_argument("--icon", type=Path, default=DEFAULT_ICON, help=f"图标 png（默认 {DEFAULT_ICON}）")
    p.add_argument("--tenant-id", default="", help="租户 ID（默认读 TENANT_ID）")
    p.add_argument("--name", default="教练模式", help="快捷入口名称（默认「教练模式」）")
    p.add_argument("--dry-run", action="store_true", help="只查询当前状态，不改动")
    p.add_argument(
        "--pc-sidebar", action="store_true",
        help="PC 端 pcUrl 用 dingtalk:// 协议强制钉钉侧边栏内嵌打开（实验，解决 PC 跳浏览器 "
             "notInDingTalk）；默认关闭=普通 https。失败回退：不带本参数重跑。",
    )
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
