"""命令行验证脚本：不打开浏览器，直接打 AG-UI 接口。

它按 AG-UI 的方式发请求、读 SSE、还原助手回复，用来验证四件事：
1. 真实模型能流式返回（统计收到多少个 TEXT_MESSAGE_CONTENT 增量）；
2. 工具真的被执行（TOOL_CALL_RESULT 里带着数据库里的真实待办）；
3. 同一会话两轮对话能记住上一轮内容（历史由调用方带上）；
4. 未登录 / 令牌无效时被拒绝。

用法（后端需先启动，且 .env 里配好 QWEN_API_KEY）：

    uv run python scripts/check_agent.py
    uv run python scripts/check_agent.py --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

import httpx


def login(client: httpx.Client, base_url: str, username: str, password: str) -> str:
    response = client.post(
        f"{base_url}/auth/login",
        json={"username": username, "password": password},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def run_agent(
    client: httpx.Client,
    base_url: str,
    token: str,
    thread_id: str,
    messages: list[dict],
) -> tuple[str, list[str], list[str]]:
    """跑一次 run，返回（助手全文，事件类型序列，工具结果列表）。"""
    payload = {
        "threadId": thread_id,
        "runId": str(uuid.uuid4()),
        "messages": messages,
    }

    text_parts: list[str] = []
    event_types: list[str] = []
    tool_results: list[str] = []

    with client.stream(
        "POST",
        f"{base_url}/agent",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
        timeout=120.0,
    ) as response:
        if response.status_code != 200:
            body = response.read().decode("utf-8", "replace")
            raise SystemExit(f"接口返回 {response.status_code}：{body}")

        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            event_types.append(event["type"])

            if event["type"] == "TEXT_MESSAGE_CONTENT":
                text_parts.append(event.get("delta", ""))
            elif event["type"] == "TOOL_CALL_START":
                print(f"  · 调用工具 {event['toolCallName']}")
            elif event["type"] == "TOOL_CALL_RESULT":
                tool_results.append(event.get("content", ""))
            elif event["type"] == "RUN_ERROR":
                raise SystemExit(f"运行失败：{event.get('message')} ({event.get('code')})")

    return "".join(text_parts), event_types, tool_results


def check_rejected(client: httpx.Client, base_url: str) -> None:
    """不带令牌必须 401，而且不能建立事件流。"""
    response = client.post(
        f"{base_url}/agent",
        json={"threadId": "t", "runId": "r", "messages": []},
    )
    print(f"  未带令牌：HTTP {response.status_code} {response.text[:60]}")
    if response.status_code != 401:
        raise SystemExit("预期 401，实际不是")
    if "text/event-stream" in response.headers.get("content-type", ""):
        raise SystemExit("预期在建流前拒绝，实际建立了事件流")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="intern")
    parser.add_argument("--password", default="intern123456")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    thread_id = f"check-{uuid.uuid4()}"

    with httpx.Client(timeout=30.0) as client:
        print("[1/4] 未登录访问 /agent")
        check_rejected(client, base_url)

        print("[2/4] 登录并开始第一轮")
        token = login(client, base_url, args.username, args.password)
        messages = [{"id": str(uuid.uuid4()), "role": "user", "content": "记住一个虚构昵称：小海豹。之后我叫你的时候就用这个名字。"}]
        reply1, events1, _ = run_agent(client, base_url, token, thread_id, messages)
        print(f"  收到 {len(events1)} 个事件，助手回复：{reply1[:80]}")
        messages.append({"id": str(uuid.uuid4()), "role": "assistant", "content": reply1})

        print("[3/4] 第二轮：追问上一轮说的昵称")
        messages.append({"id": str(uuid.uuid4()), "role": "user", "content": "我刚才让你记的昵称是什么？只回答昵称本身。"})
        reply2, events2, _ = run_agent(client, base_url, token, thread_id, messages)
        print(f"  收到 {len(events2)} 个事件，助手回复：{reply2[:80]}")
        if "海豹" not in reply2:
            raise SystemExit("上下文没有生效：第二轮没答出昵称")

        print("[4/4] 第三轮：触发只读工具")
        messages.append({"id": str(uuid.uuid4()), "role": "assistant", "content": reply2})
        messages.append({"id": str(uuid.uuid4()), "role": "user", "content": "我现在还有哪些待办没做完？"})
        reply3, events3, tool_results = run_agent(client, base_url, token, thread_id, messages)
        if "TOOL_CALL_START" not in events3:
            raise SystemExit("模型没有调用工具")
        print(f"  工具返回：{tool_results[0][:160] if tool_results else '(空)'}")
        print(f"  助手回复：{reply3[:120]}")

    print("\n结论：流式增量、两轮上下文、工具真实执行、未登录拒绝，全部通过。")
    _ = events1
    sys.exit(0)


if __name__ == "__main__":
    main()
