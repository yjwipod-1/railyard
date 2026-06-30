# Railyard 中文快速上手

## 这是什么

Railyard 是一套给 AI agent 项目用的工作流脚手架。你告诉 Planner 要做什么，Architect 拆成 ticket 分给 Runner 跑，跑完回到 Architect 审，审完回到 Planner 汇总。所有任务状态落在 SQLite 里，不靠聊天记录续命。

## 放在哪里

把 Railyard 代码放到你项目的 `railyard/` 目录下：

```bash
git clone https://github.com/yjwipod-1/railyard.git railyard
```

## 初始化

```powershell
python railyard/scripts/init_workflow.py --project-root .
```

这会在 `railyard/.workflow/` 下建 SQLite 数据库，同时写出默认 agent profiles 和文档目录骨架。

## 用哪个 session 做什么

Railyard 不要求一个 session 绑死一个 ticket。每个 role 各自开 session，干完就关。

| Role | 什么时候开 | 干什么 |
|---|---|---|
| **Planner** | 你要定方向、拆 epic、做跨 lane 决策的时候 | 把需求拆成 epic，交给 Architect 往下分 |
| **Architect** | Planner 给出了 epic 或 ticket，需要拆 ticket、审结果的时候 | 把 epic 拆成可执行的 ticket，审 Runner 产出 |
| **Runner** | Architect 分好了 ticket，需要动手实现的时候 | 按 ticket scope 干活，跑完验证，写结果 |
| **Validator** | Architect 或 Planner 需要独立验证某个 ticket 产出是否符合 contract | 只读检查，出报告，不改东西 |

一个 session 用完就扔。下次再开一个新的，读一下 `railyard/SKILL.md` 和对应的 reference 就能继续。

## 最小 prompts

以下 prompt 可以直接复制到对应 session 里用。

### Planner 起步

```text
Use this session as the Planner for my project.
Read railyard/SKILL.md and railyard/references/roles.md.
Convert our current project direction into Railyard epics and tickets.
Then give me the smallest Architect startup prompt for the next ticket or epic.
```

### Architect 起步

从 Planner 拿到启动 prompt，直接贴进去。如果手动开：

```text
Read railyard/SKILL.md, railyard/references/roles.md,
railyard/references/startup-sequence.md, and railyard/references/lifecycle.md.
role=architect
Work on <epic_id or ticket_id>.
Dispatch the Runner if your platform supports subagents.
If not, return the exact Runner startup prompt.
```

### Runner 起步（手动 fallback）

```text
Read railyard/SKILL.md.
role=runner
ticket_id=<ticket_id>
Stay inside the ticket scope, run the required validation,
and return the Runner result.
```

### Validator（需要时）

```text
Read railyard/SKILL.md and railyard/references/validator-protocol.md.
role=validator
Apply the validation contract in <contract_path> to <artifact_paths>.
Return a Validation Report JSON. Do not modify artifacts or record lifecycle transitions.
```

## 状态保存在哪里

所有工作流状态在一个 SQLite 文件里：

```text
railyard/.workflow/workflow.db
```

另外 `railyard/.railyard-workflow.json` 记录了当前生效的数据库路径，session 启动时读这个找数据库。

不要提交以下目录和文件：

- `railyard/.workflow/` — 本地工作流数据库
- `railyard/.railyard-workflow.json` — 本地路径记录
- `docs/domain/inbox/` `docs/domain/outbox/` — ticket 和结果文件
- `docs/system/inbox/` `docs/system/outbox/` — ticket 和结果文件

这些是项目运行时状态，不是 Railyard 代码本身。

## 常见边界

- **ticket 不需要绑 session**：给 session 传 ticket_id 就行，下次开新的继续用 DB 里的状态。
- **不要跨 lane 做事**：system lane 和 domain lane 分开，各自有各自的 Architect 和 Runner。
- **Runner 不拆 ticket**：Runner 只执行已就绪的 ticket，不自己改 scope、不加需求。
- **Architect 不替 Runner 写代码**：Architect 负责拆 ticket、审结果、dispatch，不亲自实现。
- **Validator 只读**：Validator 只出报告，不改文件、不改 DB、不做生命周期操作。
- **状态在 DB 里，不在聊天记录里**：session 关了就关了，下次开新的从 DB 读当前状态继续。

## 继续阅读

以下 reference 是详细的操作契约，需要时按角色查阅：

- `references/model.md` — 角色模型和职责划分
- `references/startup-sequence.md` — 完整的 session 启动流程
- `references/roles.md` — 每个角色的权限和边界
- `references/lifecycle.md` — ticket 生命周期和状态流转
- `references/routing.md` — 工作流路由规则
- `references/platform-dispatch.md` — 不同平台的 agent dispatch 适配
- `references/helper-commands.md` — 全部 helper 脚本命令参考
- `references/validation-contract.md` — 验证契约体系
- `references/validator-protocol.md` — Validator dispatch 和报告规范
- `references/result-format.md` — Runner 结果 JSON 格式

完整英文文档见 [README.md](README.md)。
