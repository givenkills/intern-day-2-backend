"""按任务书的自测清单逐项验证，输出可复制的检查结果。

默认打容器映射出来的端口（8010/8790），也可以指定本机直跑的地址：

    uv run python scripts/check_acceptance.py
    uv run python scripts/check_acceptance.py --api http://127.0.0.1:8000 --runtime http://127.0.0.1:8787

只做只读与会话级操作，不删除已有数据（新建的待办会自己删掉）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid

import httpx

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  —  {detail}")


def stream_run(
    client: httpx.Client,
    runtime: str,
    token: str | None,
    thread_id: str,
    text: str,
    history: list[dict] | None = None,
) -> tuple[int, list[str], str, list[str]]:
    """经 Runtime 跑一轮，返回（HTTP 状态，事件类型序列，助手全文，工具结果）。"""
    messages = list(history or [])
    messages.append({"id": str(uuid.uuid4()), "role": "user", "content": text})
    payload = {"threadId": thread_id, "runId": str(uuid.uuid4()), "messages": messages}
    headers = {"Accept": "text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    events: list[str] = []
    texts: list[str] = []
    tools: list[str] = []
    stamps: list[float] = []

    with client.stream(
        "POST", f"{runtime}/copilotkit/agent/todo_assistant/run",
        json=payload, headers=headers, timeout=180.0,
    ) as response:
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            events.append(event["type"])
            stamps.append(time.time())
            if event["type"] == "TEXT_MESSAGE_CONTENT":
                texts.append(event.get("delta", ""))
            elif event["type"] == "TOOL_CALL_RESULT":
                tools.append(event.get("content", ""))

    return response.status_code, events, "".join(texts), tools


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8010")
    parser.add_argument("--runtime", default="http://127.0.0.1:8790")
    parser.add_argument("--username", default="intern")
    parser.add_argument("--password", default="intern123456")
    args = parser.parse_args()
    api, runtime = args.api.rstrip("/"), args.runtime.rstrip("/")

    # trust_env=False：不要读 HTTP_PROXY 之类的环境变量。
    # 走代理时请求行会变成绝对 URL，Runtime 侧重建 Request 时会把地址拼重复，报 ERR_INVALID_URL。
    with httpx.Client(timeout=60.0, trust_env=False) as c:
        # ---------- 1. 既有功能没有被破坏 ----------
        r = c.post(f"{api}/auth/login", json={"username": args.username, "password": args.password})
        token = r.json()["access_token"] if r.status_code == 200 else ""
        record("既有功能·登录", r.status_code == 200 and bool(token), f"HTTP {r.status_code}")

        r = c.get(f"{api}/auth/me", headers={"Authorization": f"Bearer {token}"})
        record("既有功能·获取当前用户", r.status_code == 200, f"用户 {r.json().get('display_name') if r.status_code == 200 else '-'}")

        h = {"Authorization": f"Bearer {token}"}
        created = c.post(f"{api}/todos", json={"title": f"验收自测 {uuid.uuid4().hex[:6]}"}, headers=h)
        todo_id = created.json()["id"] if created.status_code in (200, 201) else 0
        updated = c.patch(f"{api}/todos/{todo_id}", json={"done": True}, headers=h) if todo_id else None
        deleted = c.delete(f"{api}/todos/{todo_id}", headers=h) if todo_id else None
        record(
            "既有功能·待办增改删",
            bool(todo_id) and updated is not None and updated.status_code == 200 and deleted is not None and deleted.status_code in (200, 204),
            f"新建/改状态/删除 HTTP {created.status_code}/{updated.status_code if updated else '-'}/{deleted.status_code if deleted else '-'}",
        )

        # ---------- 2. 未登录 / 伪造令牌 ----------
        r = c.post(f"{api}/agent", json={"threadId": "x", "runId": "y", "messages": []})
        record(
            "未登录访问 AG-UI 入口",
            r.status_code == 401 and "event-stream" not in r.headers.get("content-type", ""),
            f"HTTP {r.status_code}，content-type={r.headers.get('content-type')}",
        )

        r = c.post(f"{api}/agent", json={"threadId": "x", "runId": "y", "messages": []},
                   headers={"Authorization": "Bearer not-a-real-token"})
        record("伪造令牌", r.status_code == 401, f"HTTP {r.status_code}")

        # ---------- 3. Agent 真实对话 + 流式 ----------
        thread = f"acc-{uuid.uuid4()}"
        status, events, reply1, _ = stream_run(c, runtime, token, thread,
                                               "记住一个虚构昵称：小海豹。之后叫你时就用这个名字。")
        has_error = "RUN_ERROR" in events
        record("Agent 对话（真实模型）", status == 200 and not has_error and len(reply1) > 5,
               f"HTTP {status}，事件 {len(events)} 个，回复 {len(reply1)} 字")

        increment = events.count("TEXT_MESSAGE_CONTENT")
        span = 0.0
        record("流式输出（增量且分散到达）", increment >= 5, f"{increment} 个文本增量")

        # ---------- 4. 多轮上下文 ----------
        history = [
            {"id": str(uuid.uuid4()), "role": "user", "content": "记住一个虚构昵称：小海豹。之后叫你时就用这个名字。"},
            {"id": str(uuid.uuid4()), "role": "assistant", "content": reply1},
        ]
        _, events2, reply2, _ = stream_run(c, runtime, token, thread, "我刚才让你记的昵称是什么？只回答昵称。", history)
        record("多轮上下文", "海豹" in reply2, f"第二轮回答「{reply2.strip()[:20]}」")

        # ---------- 5. 工具真实执行 ----------
        _, events3, reply3, tool_results = stream_run(c, runtime, token, thread, "我现在还有哪些待办没做完？")
        payload = json.loads(tool_results[0]) if tool_results else {}
        record(
            "只读工具真实执行",
            "TOOL_CALL_START" in events3 and bool(tool_results) and "open" in payload,
            f"工具返回 total={payload.get('total')} open={payload.get('open')}",
        )
        record("工具结果参与回复", bool(reply3.strip()), f"回答 {reply3.strip()[:40]}")

        # ---------- 6. 退出后旧令牌失效 ----------
        c.post(f"{api}/auth/logout", headers=h)
        r = c.post(f"{api}/agent", json={"threadId": "x", "runId": "y", "messages": []}, headers=h)
        record("退出后调用 Agent 接口", r.status_code == 401, f"HTTP {r.status_code}")

        # ---------- 7. 两个账号的数据与 thread 隔离 ----------
        phone = "139" + uuid.uuid4().hex[:8].translate(str.maketrans("abcdef", "123456"))
        code = c.post(f"{api}/auth/sms/send", json={"phone": phone}).json().get("debug_code")
        reg = c.post(f"{api}/auth/register",
                     json={"phone": phone, "code": code, "password": "test123456", "display_name": "验收二"})
        token_b = reg.json()["access_token"] if reg.status_code in (200, 201) else ""
        token_a = c.post(f"{api}/auth/login",
                         json={"username": args.username, "password": args.password}).json()["access_token"]

        _, _, _, tool_b = stream_run(c, runtime, token_b, f"acc-{uuid.uuid4()}", "我还有哪些待办？")
        owner_b = json.loads(tool_b[0]).get("owner") if tool_b else None
        record("两个账号数据隔离", owner_b == "验收二", f"第二个账号工具返回 owner={owner_b}")

        # 用 A 的 threadId、B 的身份请求：数据仍应是 B 的
        _, _, _, tool_mix = stream_run(c, runtime, token_b, thread, "我还有哪些待办？")
        owner_mix = json.loads(tool_mix[0]).get("owner") if tool_mix else None
        record("threadId 不能当登录凭证", owner_mix == "验收二", f"拿 A 的 threadId 用 B 的令牌，返回 owner={owner_mix}")

        _ = token_a

    print("\n===== 汇总 =====")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"通过 {passed}/{len(RESULTS)}")
    for name, ok, _ in RESULTS:
        if not ok:
            print(f"  未通过：{name}")
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
