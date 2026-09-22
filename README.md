# Scientific Research Harness

Scientific Research Harness（SRH）是 Codex 项目的本地研究工作记忆：把已读内容、反例、假设、实验和重要决策写入项目 SQLite 数据库，供后续工作恢复。它不是独立研究平台、agent、爬虫、论文解析器、模型/API 客户端，也不会自动下载论文、运行实验或代写全文。

工作方式很小：先恢复**已保存**的本地上下文；由当前问题、探索路线、新进展或已知缺口驱动有预算的定向阅读；当学习会改变后续阅读、解释、实验或决策时，才用 `remember` 写回。当前用户请求、范围和授权永远优先于旧的 `next_action`。Stop hook 只能快照数据库，不能记住未写入的对话推理或工具输出。

## 安装与初始化

使用 Python 3.10+，并把实际研究项目放在独立目录（不是本源码仓库）：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\srh.exe init demo-project --question "我的研究问题"
```

`dev` 提供本地测试；主工作闭环不需要 embedding 模型。需要语义检索时再安装 `.[semantic]`，它提供 FastEmbed。初始化只修改目标项目的 `.research/`、明确标记的 `AGENTS.md` 块、`.agents/skills/srh-*`，以及（仅当 `--hooks`）项目 hook 文件；无关指令和 skills 会保留。已安装 skill 有冲突时会保护原文件而非静默覆盖，升级旧项目应先人工审阅。

## 短而可追溯的工作循环

现有例子完全是 synthetic：论文和摘录不是现实出版物，URL 是 `example.invalid`。

```powershell
.\.venv\Scripts\srh.exe --project demo-project ingest examples/evidence-bundle.json
.\.venv\Scripts\srh.exe --project demo-project state --file examples/state.json
.\.venv\Scripts\srh.exe --project demo-project context --intent resume --max-chars 3000
.\.venv\Scripts\srh.exe --project demo-project context --intent bridge --focus hypothesis:cross-domain-bridge --query "volatile context" --max-chars 3000
.\.venv\Scripts\srh.exe --project demo-project quality --focus hypothesis:cross-domain-bridge
```

先看本地上下文，不要自动做无限文献综述。当前问题、探索路线、新进展、来源/章节/局限/替代解释缺口都可以驱动有范围和预算的搜索或阅读；只使用 host 中实际可用的工具。记录短的来源卡和带 locator 的证据；区分作者报告、直接观察、作者解释和目标域提案。保留负面证据；绝不因名字相似而合并实体，也绝不自动升级假设。

当这些界限会影响后续决定时，可选地在 record `data`（或 edge 顶层）写
`applicability`：`domain`、`source_domain`、`target_domain`、`task_context` 是
作者明确给出的标签，不会按名称相似自动匹配。evidence 可用
`evidence_kind` 标为 `observation`、`author_report`、`interpretation` 或
`planned`；`assertion_role` 可标为 source/target observation 或 assertion、
`transfer_proposal`、`analogy`、`limitation`、`interpretation`。有
`evidence_ids` 的 record/edge 可用 `evidence_roles` 将每个已列 evidence 标为
`support`、`counterevidence`、`context` 或 `analogy`。这些是可检查的
provenance/scope 声明，不是来源验证、跨域推断或科学结论。

下列路径可从一个全新空项目运行，不需要 index、模型或网络；fixture 仍然全是 synthetic：

```powershell
.\.venv\Scripts\srh.exe init workflow-demo --question "Synthetic workflow only"
.\.venv\Scripts\srh.exe --project workflow-demo remember examples/workflow-remember-delta.json
.\.venv\Scripts\srh.exe --project workflow-demo state --file examples/workflow-context-state.json
.\.venv\Scripts\srh.exe --project workflow-demo context --intent bridge --focus hypothesis:workflow-transfer --max-chars 1800
.\.venv\Scripts\srh.exe --project workflow-demo quality --focus hypothesis:workflow-transfer
```

## 与当前任务匹配的上下文

`context` 完全离线。给出 `--query` 时也只做词法检索，不加载模型或联网。`--intent` 是用户/Codex 显式说明此次需要恢复什么，不是隐藏路由器：

```text
resume      问题、未解决工作和最近已保存决策
map         缺口、检索 provenance 和反证
read        在阅读前限定来源或章节范围
bridge      类比前提、失败条件与目标测试
experiment  最便宜的判别性实验及可复现字段
analyze     已记录结果、反证与局限
```

`--focus ID` 可重复。明确焦点优先于无关旧状态和旧 `next_action`；`--max-chars` 会诚实标出截断，遗漏的内容不等于不存在：

```powershell
.\.venv\Scripts\srh.exe --project demo-project context --intent read --focus paper:synthetic-source --max-chars 1200
.\.venv\Scripts\srh.exe --project demo-project context --intent experiment --focus experiment:bridge-test --focus hypothesis:cross-domain-bridge
```

## 用 `remember` 保存显著学习

不要为每次工具调用都创建记录。新来源、反例、边界条件、结果、决策或状态转换会影响今后工作时，才使用 `remember`。它以原子方式写入 records、edges 和可选 `state_patch`；状态哈希不匹配、引用错误或基线不满足时，什么也不写。首次写入新实体无需手造 hash 或 review receipt。

首次新增记录可直接写入；若 delta 同时修改状态或需要避免覆盖并发状态，再先取得项目特定的状态哈希：

```powershell
.\.venv\Scripts\srh.exe --project demo-project state --hash
.\.venv\Scripts\srh.exe --project demo-project remember examples/workflow-remember-delta.json

Get-Content examples/workflow-remember-delta.json -Raw |
  .\.venv\Scripts\srh.exe --project demo-project remember -
```

`examples/workflow-remember-delta.json` 是可从空项目直接运行的首个 delta；它让 SRH 自动捕获新引用的基线。修改既有工作或同时 patch state 时，可使用下面完整 shape，把 `expected_state_hash` 的占位符替换为 `state --hash` 输出。

```json
{
  "schema_version": 1,
  "expected_state_hash": "<state --hash.state_hash>",
  "records": [{
    "id": "decision:volatility-first", "type": "decision",
    "title": "Test volatility before broad transfer",
    "summary": "The opposing finding changes the cheapest next experiment.",
    "tags": ["synthetic"],
    "data": {
      "reason": "The observed effect disappeared under volatile context.",
      "evidence_ids": ["evidence:opposing-finding"],
      "next_action": "Compare stable and volatile context before expanding the transfer claim."
    }
  }],
  "edges": [],
  "state_patch": {"next_action": "Compare stable and volatile context before expanding the transfer claim.", "recent_decisions": ["decision:volatility-first"]}
}
```

新实体会捕获直接引用记录的 `data.basis`（edge 为顶层 `basis`）。来源变化会让下游需要复核，而不改变其真值状态。普通 `ingest` 更新既有 record/edge 时不能提交不同的 `basis` 或 `basis_review`，且省略它们会保留既有维护字段；把 export 恢复到一个新的空项目仍可带入这些字段。只有在来源变更后确实重读既有实体时，才给 `reviewed_ids` 和 `review_note`，留下 `basis_review` receipt；receipt 进入下游 dependency hashing，所以上游复核不会让下游悄悄变 fresh。没有基线的旧记录保持 legacy/unknown。record 与 edge ID 冲突时，仅 quality 和 review ID 使用 `record:<id>` 或 `edge:<id>`；context 的 `--focus` 目前接受记录 ID。

`delta_id` 是可选的重试键，而不是每次 `remember` 的必填项。把非空字符串加入**第一次提交前的完整 delta**，例如 `"delta_id": "handoff-retry-01"`；同一键和相同的完整输入重放时，会返回先前结果并带 `"replayed": true`，不会写入新事件或新的 review receipt。同一键配不同输入会报错。未提供 `delta_id` 时，每次显式 `reviewed_ids`/`review_note` 复核都是新的 receipt，不能把一般 `remember` 当作幂等操作。

仅更新状态时同样可避免覆盖并发变更：

```powershell
.\.venv\Scripts\srh.exe --project demo-project state --file patch.json --expected-hash <state-hash>
```

`examples/workflow-context-state.json` 展示已保存的焦点转换；它不会强迫当前请求遵从旧 next action。

## 检查图质量，但不伪装成科学评分

```powershell
.\.venv\Scripts\srh.exe --project demo-project quality --focus hypothesis:cross-domain-bridge --limit 30
```

`quality` 返回 `scope`、`findings`、`counts` 和 `truncated`。每个 finding 包含 `code`、`severity`、`entity_ids` 和 `message`；`subject_id` 标出负责处理的实体，适用时 `subject_kind` 是 `record` 或 `edge`，而 `entity_ids` 仍保留完整 provenance。它指出 dependency drift/未跟踪基线、缺 read scope 或 conditions、重复 DOI/arXiv 候选以及不同条件下反证的 `potential_disagreement`。对标为 `supported` 的 target hypothesis，只有带 `support` role、`target_observation` 和相同明确 target domain 的 evidence 才是记录的 target support；否则会出现 `transfer_support_gap`（claim 对应 `claim_target_support_gap`）。来源证据、counterevidence、类比和 limits 仍只是 provenance，不会自动变成 target observation。此类缺口只是建议性提示：不改变 status、不强迫本地实验，也不构成任何新颖性或科学结论；edge 的 evidence applicability 不匹配同样只是 provenance warning。所有这些不是可信度分数、来源忠实度证明、因果识别或科学真理；Codex 仍须实读指定来源/章节和 locator。

## 检索、可信边界与 hooks

默认索引是英文 FastEmbed `BAAI/bge-small-en-v1.5`；首次显式 index 可下载模型：

```powershell
.\.venv\Scripts\srh.exe --project demo-project index --provider fastembed --model BAAI/bge-small-en-v1.5
.\.venv\Scripts\srh.exe --project demo-project search "cue gating context" --mode hybrid --limit 5
```

该模型不保证中文、跨语种或专业领域检索。查询绝不会悄悄下载模型或替换为 hash；hash 只是不具备语义能力的离线词法特征哈希，不能用来声称科学效果。

普通 `context` 默认附带有界的 quality advisory；它的缺口和 `truncated` 仍须用显式 `quality` 或实际阅读核查。`srh init --hooks` 安装快速、离线、有界的 SessionStart/Stop handler。请在 Codex `/hooks` 审查并信任精确 handler；项目 hook 在项目配置层被信任前可能不可用。为保持启动/停止路径短且可预测，hook 故意跳过 quality、语义/vector 检索和全语料审计，只读取已经保存的 SQLite 数据。没有 hook 时显式运行 `context` 与 `remember`/`checkpoint`。hook 从不加载模型、索引、联网、运行实验或续写 turn。

## 命令速查

```text
srh init [PATH] [--question TEXT] [--hooks]
srh ingest FILE                 # FILE - 从 stdin 读取
srh remember FILE               # FILE - 从 stdin 读取；原子工作记忆 delta
srh show ID | list [--type TYPE]
srh search QUERY [--mode lexical|vector|hybrid] [--type TYPE] [--limit N]
srh graph [ID] [--hops N] [--limit N]
srh quality [--focus ID] [--limit N]
srh index [--provider fastembed|hash] [--model NAME]
srh state [--hash] | state --file PATCH_JSON [--expected-hash HASH]
srh context [--intent resume|map|read|bridge|experiment|analyze] [--focus ID ...] [--query TEXT] [--max-chars N]
srh checkpoint [--note TEXT] | doctor | export [FILE]
srh hook session-start|stop
```

导出 JSON/Markdown 是可检查的派生快照；SQLite 才是唯一可变事实源。不要将模型缓存、数据库或私人研究资料提交到源代码库。
