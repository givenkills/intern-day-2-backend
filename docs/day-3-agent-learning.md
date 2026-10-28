# 第三天：把 Agent 接进既有项目，我理解的交互链路

> 这份文档解释的是**我实际跑起来的这套实现**，不是通用介绍。
> 任何和代码不一致的地方，以仓库里的代码为准。

## 0. 一句话版本

Microsoft Agent Framework 负责「想」，CopilotKit 负责「聊」，AG-UI 负责「怎么把想的过
程一段一段传过去」。三者不是替代关系，是三层不同职责。

## 1. Microsoft Agent Framework 解决什么问题？

它解决的问题是：**别让我自己写「调模型 → 解析工具调用 → 执行工具 → 把结果塞回模型 → 再调一次」这个循环。**

没有框架时，一次带工具的对话我要手动做这些事：

1. 拼 messages 数组；
2. 带上 tools 的 JSON Schema 请求模型；
3. 判断返回里有没有 `tool_calls`；
4. 有的话按名字找到本地函数、解析参数、执行；
5. 把执行结果包成 `role=tool` 的消息再请求一次；
6. 循环直到模型不再要工具；
7. 全程还要处理流式增量和异常。

Agent Framework 把 2 到 6 收进了一个 `Agent` 对象里。代码里我只写三样东西：

```python
Agent(
    name="todo_assistant",
    instructions=INSTRUCTIONS,          # 它是什么、能做什么、不能做什么
    client=OpenAIChatCompletionClient(...),  # 用哪个模型
    tools=[list_my_todos],              # 它能调用什么
)
```

三者的关系我这样理解：

- **模型**是「会说人话但只会输出文本」的那部分。它自己碰不到数据库，也不知道真实数据。
- **工具**是「能碰真实世界但要人喂参数」的那部分。它不知道什么时候该被调用。
- **Agent** 是中间那个调度者：把工具的名字和参数说明翻译成模型能懂的 schema，模型说「我要调
  list_my_todos，参数 status=open」，Agent 就去执行，再把结果作为一条新消息交回模型，让模型
  继续把话说完。

所以工具的执行发生在**后端进程里**，不在模型里，也不在浏览器里。这一点是后面所有安全性讨论
的基础。

## 2. CopilotKit 在 React 应用里做什么？和我自己写个输入框有什么区别？

我原本以为它就是个聊天 UI 组件库，实际用下来它解决的是**协议对接**，UI 只是顺带给的。

自己写输入框，我要做的是：

- 维护 messages 数组、滚动到底、发送中禁用按钮、失败提示；
- 自己定一套请求格式，自己定「流式片段长什么样」；
- 自己处理「工具调用中」这种中间状态怎么显示；
- 自己处理中断、重试、多轮拼接。

用 CopilotKit，`<CopilotChat />` 直接拿到了消息列表、输入框、流式渲染、工具调用卡片、错误
提示；更重要的是它内置了**和 AG-UI 兼容的请求格式**，所以我不需要自己发明一套协议。

真正的差别在于：如果我自己写，我写的是「我的前端和我的后端之间的私有协议」；用 CopilotKit +
AG-UI，我写的是「任何 AG-UI Agent 都能对接的标准协议」。将来后端从 Python 换成 .NET，前端一
行不用改。

代价是：**它有一层 Runtime**。这层不能省（见第 5 节），必须多跑一个 Node 服务。

## 3. AG-UI 是什么？和普通 JSON 接口、HTTP、SSE 什么关系？

### 3.1 它不是框架，是「事件的约定」

AG-UI 规定的是：**一次 Agent 运行过程中，服务端应该按什么顺序、发哪些类型的事件，每个事件带
什么字段。**

我实际在 Network 里看到的顺序是这样的（简化）：

```
RUN_STARTED           一次运行开始，带 threadId 和 runId
TEXT_MESSAGE_START    模型开始说一段话
TEXT_MESSAGE_CONTENT  这段话的第 1 个增量  ← 一个 token 或几个 token
TEXT_MESSAGE_CONTENT  第 2 个增量
...
TEXT_MESSAGE_END      这段话结束
TOOL_CALL_START       模型决定调用工具，给名字
TOOL_CALL_ARGS        工具参数（可能分几段传）
TOOL_CALL_END
TOOL_CALL_RESULT      后端执行完，把结果作为事件发出来
TEXT_MESSAGE_START    模型拿着工具结果继续说
...
RUN_FINISHED          整个运行结束
```

失败时则是 `RUN_ERROR`，带 `message` 和 `code`。

关键点：**这套事件是「语义」，不是「传输」。** 同样的 RUN_STARTED，可以用 SSE 发，可以用
WebSocket 发，也可以一次性塞进一个 JSON 数组返回。

### 3.2 和普通 JSON 接口的区别

普通接口是「一问一答」：请求发完，服务端算完，返回一个完整 JSON。中间过程对客户端不可见。

AG-UI 接口是「一问，过程中不断告诉你进展，最后一句话说完」。它必须解决三个普通 JSON 不需要
解决的问题：

- 边界：怎么知道一段文字结束了、下一个事件开始了；
- 增量：模型是逐字生成的，怎么把增量转给前端；
- 中间状态：工具调用算不算「回复的一部分」，前端要不要显示。

### 3.3 和 HTTP、SSE 的关系

这三者不在一个层面上，我一开始也混了：

| 概念 | 层面 | 在我们项目里的位置 |
| --- | --- | --- |
| HTTP | 传输层协议 | `POST /agent`，请求体是 JSON |
| SSE | HTTP 上的一种「响应体格式」 | `Content-Type: text/event-stream`，一行行 `data: {...}` |
| AG-UI | 应用层事件协议 | 那些 `data:` 里面装的是 RUN_STARTED 之类的事件 |

也就是说：**SSE 是信封，AG-UI 是信。** 我可以换信封（比如换 WebSocket）而不改信的内容。

### 3.4 SSE 不是 WebSocket

这一条我特意写下来，因为很容易说错：

- SSE 是**单向**的：服务端 → 浏览器。客户端要说话，只能再发一个普通 HTTP 请求。
- SSE 是**普通 HTTP 响应**：一个请求，响应体不结束，一直往里写。用 `fetch` 就能读，不需要升级协议。
- WebSocket 是**双向**的，需要 `Upgrade` 握手，连接建立后两边都能随时发。

我们的实现里没有任何 WebSocket。浏览器发一个 `POST /agent`，服务端返回 200 和一个不结束的
`text/event-stream` 响应体，事件一条条写进来。

还有一个细节：AG-UI 用的是 **POST + SSE**，不是浏览器原生的 `EventSource`。因为 `EventSource`
只支持 GET，装不下 JSON 请求体（消息历史、工具列表都在请求体里）。所以前端是用 `fetch` 读
`response.body` 这个流，自己按 SSE 格式切行。

## 4. 从点击发送到看到回复，消息经过了哪些组件

我们这套实际拓扑（三个进程）：

```
浏览器 :5173
  │ ① POST /copilotkit/agent/todo_assistant/run
  │    Header: Authorization: Bearer <登录令牌>
  ▼
CopilotKit Runtime :8787  (前端仓库里的 Express 服务)
  │ ② 收到请求 → 按请求解析 agents → 得到 HttpAgent
  │    POST http://backend:8000/agent   (浏览器访问时是 http://localhost:8000/agent)
  │    Header: Authorization 原样带过去
  ▼
FastAPI :8000  POST /agent
  │ ③ authorization_scope_resolver 先跑：查 sessions 表验证令牌
  │    失败 → 401 JSON，事件流根本不会建立
  │    成功 → 把 user 写进 ContextVar，返回 scope "user:1"
  │ ④ Agent Framework 组装 messages + tools schema，请求模型
  ▼
千问（DashScope 兼容端点）
  │ ⑤ 流式返回；如果模型要调工具，Agent 在后端执行 list_my_todos
  │    工具用 ContextVar 里的 user.id 查库 → 结果回到 Agent → 再请求模型
  ▼
AG-UI 事件流（SSE）逐条回传
  │ ⑥ Runtime 把事件流转回浏览器
  ▼
浏览器：SSE 增量 → CopilotChat 更新当前那条气泡
```

每一步的输入输出：

| 步骤 | 组件 | 收到什么 | 返回什么 |
| --- | --- | --- | --- |
| ① | CopilotChat | 用户输入的文字 | 一次 run 的 HTTP 请求（含完整历史消息） |
| ② | Runtime | CopilotKit 请求 + 认证头 | AG-UI `RunAgentInput`，带同一个认证头 |
| ③ | FastAPI 依赖 | `Authorization` | 校验通过的用户 dict / 401 |
| ④ | Agent | 消息列表 + 工具 schema | 对模型的请求 |
| ⑤ | 模型 | messages + tools | 文本增量，或 tool_calls |
| ⑥ | 工具 | Agent 传的参数（没有 user_id） | JSON 字符串结果 |
| ⑦ | 前端 | 事件流 | 逐步变长的气泡 |

**第 ⑥ 步是这次最值得说的一步**：工具签名里没有 `user_id`。范围不是「模型传对参数」保证的，
而是「后端只用登录令牌对应的那个 id 查库」保证的。

## 5. Runtime 部署在哪、做什么？为什么 Vite 不能当 Runtime？

### 5.1 它是什么

`server/copilot-runtime.mjs`，一个 Express 服务，跑在容器里的 `runtime` 服务（宿主机
`:8787`）。它和前端共用同一个镜像，只是启动命令不同。

它做三件事：

1. **协议搬运**：浏览器说 CopilotKit 的接口，它翻译成 AG-UI 的 `RunAgentInput` 发给 Python。
2. **按请求解析 Agent**：`agents` 写成函数而不是固定对象：

   ```js
   agents: ({ request }) => {
     const authorization = request.headers.get("authorization") ?? "";
     return { todo_assistant: new HttpAgent({ url: AGENT_URL, headers: { Authorization: authorization } }) };
   }
   ```

   写成函数是**必须的**。如果写成固定对象，整个进程只有一个 Agent 实例、一份头信息，所有用户
   都会被当成同一个人——这就是任务说明里「Runtime 不得代替所有用户使用同一个特权凭证」要避免的事。

3. **原样转发事件流**：不缓冲、不拼接，收到一条转发一条。

### 5.2 为什么 Vite 不行

Vite 的 dev server 只会做两件事：把源码转成浏览器能跑的模块、把静态文件发出去。它没有
`/copilotkit/agent/:agentId/run` 这类路由，也不知道怎么把请求改写成 AG-UI 格式、怎么带着
用户的 token 去连后端。

我可以给 Vite 配 `server.proxy` 把 `/copilotkit` 转发到别处，但那只解决「转发」，不解决
「把 CopilotKit 协议翻译成 AG-UI 协议」。翻译这一步总得有个进程做，所以必须有 Runtime。

### 5.3 三个地址别混

这是最容易写错的地方，我一开始也混了：

| 谁在访问 | 该用哪个地址 | 不能写什么 |
| --- | --- | --- |
| 浏览器 → 前端 | `http://localhost:5173` | `frontend:5173` |
| 浏览器 → Runtime | `http://localhost:8787/copilotkit` | `runtime:8787` |
| Runtime 容器 → 后端 | `http://backend:8000/agent` | `localhost:8000` |
| 浏览器 → 后端（直连调试用） | `http://localhost:8000` | `backend:8000` |

原因：`localhost` 在容器里指的是容器自己；服务名只在共享的 Docker 网络里能解析，浏览器不在
那个网络里。两个仓库是两个独立的 Compose 项目，所以必须显式建共享网络：

```bash
docker network create intern-agent-net
```

否则 Runtime 里的 `backend` 这个名字根本解析不到。

## 6. 流式回复怎么逐步显示？开始、增量、结束、失败怎么识别

前端不需要自己解析 SSE（CopilotKit 做了），但我要知道它依赖哪些事件，才能判断「真流式」还是
「假打字」。

- **开始**：`RUN_STARTED`。从这一刻起界面进入「发送中」，停止按钮出现，输入框禁用。
- **增量**：每来一个 `TEXT_MESSAGE_CONTENT`，就把它的 `delta` 拼到当前气泡末尾。判断是不是
  真流式的办法不是看动画，而是看 Network：事件是不是**陆续**到达。如果所有 `data:` 在最后
  200ms 内一起出现，那就是被缓冲了。
- **工具调用**：`TOOL_CALL_START` / `TOOL_CALL_RESULT`。这时文字会停住，界面上出现「正在
  调用工具」，然后继续输出。
- **结束**：`RUN_FINISHED`，退出加载态。
- **失败**：`RUN_ERROR`，带 `code`。前端 `onError` 里收口：如果是 401 相关的，提示重新登录；
  其他错误显示原因并**结束等待**，不能一直转圈。

我在实现里踩过一个具体的坑，写在第 8 节。

## 7. 登录凭证和模型密钥分别由谁保存、发给谁

这两个东西的流向完全不同，混在一起就会出事。

### 7.1 登录令牌

- **谁产生**：后端。登录成功时随机生成 32 字节不透明字符串，数据库里只存它的 SHA-256 指纹。
- **谁保存**：浏览器 `localStorage`（沿用第二天方案）。
- **发给谁**：每个请求放在 `Authorization: Bearer ...` 里，发给 Runtime，Runtime 再原样
  发给 Python。Runtime 全程**不解析、不缓存、不替换**它。
- **谁校验**：只有 Python 后端。Runtime 不校验，前端也不校验。前端隐藏入口只是体验，不是安全边界。

### 7.2 模型密钥

- **谁保存**：只有后端进程的环境变量（`.env` / compose 的 `environment`）。
- **发给谁**：只发给模型服务商。浏览器、Runtime、聊天消息里都不出现。
- **为什么不能进浏览器**：浏览器里的任何东西用户都能看到（F12 → Network 就是明文）。一旦进
  前端，等于把公司额度公开了，而且没法限流、没法审计、没法在泄露后只换一个地方。

具体到代码：`QWEN_API_KEY` 只被 `build_agent()` 读取，而 `build_agent()` 只在后端启动时跑。
镜像构建参数（`ARG`/`ENV`）里也没有它——构建参数会被写进镜像层历史，`docker history` 就能翻出来。

我特意做的一个小设计：**没配密钥时 `/agent` 根本不注册**。这样不会出现「接口在、但一调就
500」的状态，登录和待办功能也完全不受影响。

## 8. 对话上下文从哪来？threadId 和登录会话有什么区别？

### 8.1 上下文

今天这套实现里，**对话历史由浏览器持有**。每次 run 的请求体里带着这一轮的完整 messages 数组，
后端不落库。所以：

- 同一页面里连续追问 —— 上下文在，因为历史被一起发过去了；
- 刷新页面 —— 前端消息列表重新开始，历史没了（README 里写明了）；
- 后端重启 —— 对上下文没有影响（因为后端本来就不存）。

Python 侧的 AG-UI 适配包支持把 thread 快照存起来，但那是 opt-in 的，我们没开。任务说明允许
「只保存当前会话」，我们更进一步，连服务端都不存。

### 8.2 threadId ≠ 登录会话

这两个东西长得很像，但完全不是一回事：

| | 登录会话 | AG-UI thread |
| --- | --- | --- |
| 标识 | `Authorization` 里的不透明令牌 | `threadId`（前端生成的 UUID） |
| 谁签发 | 后端，登录时 | 前端，新建对话时 |
| 作用 | 证明「你是 user 1」 | 区分「这是第几个对话」 |
| 泄露后果 | 别人能冒充你 | 只是知道有个对话存在，**拿不到内容** |

我特意验证过这一点：把 `threadId` 换成别人的，请求依然以**你自己的身份**执行，因为鉴权只看
`Authorization`。如果哪天代码变成「凭 threadId 就能读历史」，那就是把会话标识当成了凭证，是
错的。

顺带说一个官方设计：`authorization_scope_resolver` 的返回值（我们返回 `"user:1"`）会作为
thread 快照的归属范围。也就是说，即使将来开了快照持久化，A 用户也读不到 B 用户的 thread——
隔离是在框架层做的，不是靠前端自觉。

### 8.3 退出和换账号

- 退出：清 `localStorage` 里的令牌 + 清当前用户的 threadId（存在 `sessionStorage`，按 user id 分开）。
- 换账号：另一个用户的 threadId 键完全不同，不会接上前一个人的会话。
- 前端 Provider 用 `key={threadId}`，换 thread 时整个组件重新挂载，客户端消息列表随之清空，
  不会出现「新会话里还挂着旧气泡」。

## 9. 实际版本与依赖

后端（Python 3.12 镜像 / 本机 3.13）：

| 包 | 版本 | 说明 |
| --- | --- | --- |
| agent-framework-core | 1.23.0 | Agent、工具、模型客户端 |
| agent-framework-openai | 1.17.0 | OpenAI 兼容客户端（千问走这个） |
| agent-framework-ag-ui | 1.7.0 | AG-UI 适配与 FastAPI 端点 |
| ag-ui-protocol | 0.1.22 | 协议定义 |
| fastapi | 0.141.1 | Web 框架 |
| uvicorn | 0.54.0 | ASGI 服务器 |
| sse-starlette | 3.6.0 | SSE 响应 |

前端（Node 22）：

| 包 | 版本 |
| --- | --- |
| @copilotkit/react-core | 1.82.0 |
| @copilotkit/runtime | 1.82.0 |
| @ag-ui/client | 1.0.3 |
| react | 19.1.0 |
| vite | 7.3.7 |

**没有用 `agent-framework` 这个元包。** 它会把 Azure、Anthropic、Gemini、Qdrant、mem0 全部
装上（我试过一次，解析出 227 个包，光 `claude-agent-sdk` 就 123MB）。只装 `-core` +
`-openai` + `-ag-ui` 三个就够了，镜像小一个数量级。

依赖锁文件：`uv.lock`（后端）、`pnpm-lock.yaml`（前端），都提交了。

## 10. 参考的官方资料

- Agent Framework 仓库：https://github.com/microsoft/agent-framework
- Agent Framework 总览：https://learn.microsoft.com/en-us/agent-framework/overview/
- AG-UI 集成文档：https://learn.microsoft.com/en-us/agent-framework/integrations/ag-ui/
- Python AG-UI README（**最关键的一篇**，`authorization_scope_resolver` 的用法在这里）：
  https://github.com/microsoft/agent-framework/blob/main/python/packages/ag-ui/README.md
- CopilotKit 仓库：https://github.com/CopilotKit/CopilotKit
- CopilotKit 架构：https://docs.copilotkit.ai/concepts/architecture
- CopilotKit + Microsoft Agent Framework：https://docs.copilotkit.ai/microsoft-agent-framework
- AG-UI 协议：https://docs.ag-ui.com/introduction
- 千问兼容模式：https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope

## 11. 看完还是没完全弄明白的问题

1. **CopilotKit 的 `messageFilter` 和 AG-UI thread 快照同时开启时，历史会不会重复？**
   现在后端不开快照、前端全量发，所以不冲突。但如果以后要服务端存历史，两边都存一份，模型
   可能看到双重历史。文档说 `messageFilter` 会「修好断掉的 tool-call 配对」，但我没验证过
   两边同时开时的实际效果。

2. **qwen3.8-flash 是推理模型，它的 `reasoning_content` 在 Agent Framework 里怎么透出？**
   兼容端点会单独返回 `reasoning_content` 字段，我在事件流里没看到对应的 AG-UI 事件。可能是
   被适配层丢了，也可能需要显式开启某个选项。今天没影响功能，但「思考过程要不要给用户看」是
   个产品问题。

3. **`authorization_scope_resolver` 返回值除了做快照归属，还会影响什么？**
   我只验证了它必须返回非空字符串、且 401 会在建流前抛出去。它和 `single_user=True` 是互斥
   的两种模式，但内部还有没有别的行为差异（比如并发 run 的锁粒度），我没读到底层实现。

## 12. 两个只有调试才弄懂的细节

**问题**：我用 curl 直接打 `POST /agent` 不带令牌，拿到的是标准的 `401 application/json`，
很干净。但在浏览器里点发送，Network 里那条请求是 **200**，内容是 `RUN_ERROR`，message 写着
`HTTP 401: {"detail":"未登录或缺少登录凭证"}`。

一开始我以为鉴权没生效，或者是 Runtime 把 401 吞了。

**原因**：401 确实发生了，但发生在**第二跳**。浏览器请求的是 Runtime，Runtime 已经先给浏览器
回了 200 和 SSE 响应头（它必须这么做，因为它不知道后面会不会 401）。等它去请求 Python 时，
Python 才返回 401，这时候能用的通道只剩这个已经打开的 SSE 流，于是它把 401 包成一条
`RUN_ERROR` 事件发回来。

**结论**：这件事没有 bug，但意味着「401 处理」要写两处：

- 直接用 curl / 前端直连后端时：读 HTTP 状态码；
- 经过 Runtime 时：读 `RUN_ERROR` 的 message 里有没有 401。

前端 `onError` 里我两种都匹配了，并且无论哪种都**结束加载态**——这是自测清单里
「异常处理：页面明确提示失败、结束加载」要的东西。

这件事让我第一次真正分清「HTTP 状态码」和「事件流里的事件」是两层：一旦流建立起来，错误就
只能以事件的形式出现，状态码已经发出去改不了了。

### 12.2 `runtimeUrl` 是前缀，不是主机

**现象**：页面能打开、登录正常、输入框能打字，但点发送毫无反应；CopilotKit 自带的检查器显示
`No agents available`、`RUNTIME: Offline`，可我用 curl 打 `http://127.0.0.1:8787/health` 是通的。

**过程**：给 Runtime 加了一行请求日志（只打方法、路径和有没有带认证），刷新页面后看到浏览器发的是：

```
OPTIONS /info auth=no
GET     /info auth=yes
POST    /          auth=yes
```

全是根路径下的 `/info`、`/`，而我的路由前缀是 `/copilotkit`，所以这些请求全部 404。

**原因**：从 1.82 起，`runtimeUrl` 这个参数的含义变成了**接口前缀本身**，客户端会在它后面直接拼
`/info`、`/agent/<id>/run`。我按旧版习惯只写了 `http://localhost:8787`（主机），等于把前缀设成了
根路径。

**改法**：`VITE_COPILOT_RUNTIME_URL=http://localhost:8787/copilotkit`，一行就通了。

**收获**：报错说「Offline」时不要先怀疑网络，先看**实际请求的路径**。也很庆幸当时顺手加了请求日志，
否则只能对着一个没有信息的错误页猜。
