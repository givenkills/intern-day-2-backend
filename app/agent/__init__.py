"""Agent 相关代码：模型客户端、只读工具、AG-UI 入口。

拆成几个小文件是为了让「谁负责什么」一眼能看出来：
- context.py  一次请求里，工具怎么知道当前是谁
- tools.py    只读工具的真实实现
- factory.py  怎么把模型、指令、工具组装成一个 Agent
- endpoint.py 怎么把 Agent 挂到 FastAPI 上，并在这里做登录校验
"""
