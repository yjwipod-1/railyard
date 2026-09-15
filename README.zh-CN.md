# Railyard 中文快速上手

## 这是什么

Railyard 是一套给 AI agent 项目用的工作流脚手架。你告诉 Planner 要做什么，Architect 拆成 ticket 分给 Runner 跑，跑完回到 Architect 审，审完回到 Planner 汇总。所有任务状态落在 SQLite 里，不靠聊天记录续命。

本地 v0.8 功能已实现且可确定性验证：运行时状态 reducer、journal、projection、sidecar、evidence export、运行时 adapter、Gate Decision、Action Policy、带 dispatch 和 publish bridge 的 Validator Mesh、staging-manifest authority，以及公开 smoke runner。请参阅下方的“本地 v0.8 验证与发布就绪性”和[smoke 快速上手](examples/runtime_v080_smoke/README.md)。

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

## 本地 v0.8 验证与发布就绪性

从仓库根目录执行这一条本地 v0.8 验证路径。运行时仍只使用标准库；测试和验证路径使用 `requirements-test.txt` 中的两个直接测试依赖。smoke 工作目录必须由调用者提供，并且位于源码 checkout 之外；不要使用源码内的 `.tmp`、`.workflow`、cache 或 evidence 路径。

PowerShell：

```powershell
$smokeRoot = Join-Path $env:TEMP "Railyard v0.8 smoke"
python -m pip install -r requirements-test.txt
python -m compileall -q scripts
python scripts/validate_artifacts.py --project-root .
python scripts/test_runtime_v080_ci.py
python scripts/runtime_v080_regression.py
python scripts/runtime_v080_smoke.py --tmp-dir $smokeRoot --all run
```

POSIX shell：

```bash
smoke_root="${TMPDIR:-/tmp}/railyard v0.8 smoke"
python -m pip install -r requirements-test.txt
python -m compileall -q scripts
python scripts/validate_artifacts.py --project-root .
python scripts/test_runtime_v080_ci.py
python scripts/runtime_v080_regression.py
python scripts/runtime_v080_smoke.py --tmp-dir "$smoke_root" --all run
```

冻结的 catalog 有 20 个场景。003-011 是 9 个预期的、带类型的非通过 Validator Mesh 结果；另外 11 个覆盖正常、篡改、恢复和可见性路径。正确的全场景 conformance 运行会报告 `total=20`、`passed=20`、`failed=0` 并以 0 退出。带类型的非通过 `final_verdict` 是预期场景数据，不是 smoke 失败。唯一的 smoke CLI 是 `scripts/runtime_v080_smoke.py`；其组件模块是 import API。

仓库已配置 Windows 和 Linux GitHub Actions 来运行这条本地路径。该 hosted CI 配置已在本地验证，但在没有 Human 授权的 staging 和 push 前没有远程执行。Railyard 没有 hosted runtime 或 service、scheduler、proprietary provider 或 model、Knowledge extraction 或 store、vector database、RAG implementation，也没有自动 release、tag、commit 或 push。

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
- `references/knowledge-contract.md` — Knowledge 契约和知识本体
- `references/result-format.md` — Runner 结果 JSON 格式

## 治理声明

本 README 为非规范性指南（Guide）。规范性规则可通过以下文件查找：

- [治理文档分类](references/governance-document-taxonomy.md) — 定义文档类型、权威等级及优先级模型。
- [治理文档清单](references/governance-document-inventory.json) — 所有治理文档的机器可读清单及分类。
- [治理文档清单](references/governance-document-inventory.md) — JSON 清单的人工可读版本。
- [治理读路由](references/governance-read-routing.json) — 声明式路由注册表，按角色产出确定性的启动阅读清单。

按角色解析规范性读物：

```powershell
python scripts/governance_read_router.py --role architect
```

解析器返回 `status: ready` 并附带排序后的 `normative_reads` 列表，路由配置无效时则返回 `status: blocked`。

Agent 行为的规范性规则请查阅清单中链接的 Protocol、Policy、Contract、Schema、Registry 文档。

---

完整英文文档见 [README.md](README.md)。
