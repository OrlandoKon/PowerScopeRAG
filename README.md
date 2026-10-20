# PowerScopeRAG

面向雄安新区 400V 低压配电网停电原因分析的 Agentic RAG 知识检索服务。

---

## 知识库内容

| 类别 | 数量 | 说明 |
|------|------|------|
| 行业标准（regulation） | 104 份 / 7418 个分块 | 判断阈值、技术要求与操作依据 |
| 期刊与参考资料（journal） | 23 份 / 412 个分块 | 配网故障、低电压、分布式电源等研究 |
| 学位论文（thesis） | 4 份 / 649 个分块 | 配电网运行与故障分析研究 |
| **知识文档合计** | **131 份 / 8479 个分块** | 标准、规程和研究资料统一检索 |
| 历史案例库 | 34 条结构化案例 | 停电现象、处置过程与原因判定 |

---

## API 地址

```
https://rag.orlando.ink
```

---

## 接口说明

对外业务接口统一为 `POST /rag`。多智能体系统默认获取结构化证据；需要RAG直接回答时设置 `generate_answer: true`。

| 接口 | 用途 |
|---|---|
| `GET /` | 知识库状态看板 |
| `GET /health` | 服务健康检查 |
| `POST /rag` | Agentic RAG取证，可选生成回答 |

请求示例：

```bash
curl -X POST https://rag.orlando.ink/rag \
  -H "Content-Type: application/json" \
  -d '{
    "query": "某400V台区全部停电，低压总开关过流动作，应该查找哪些判断依据和相似案例？",
    "max_follow_up_rounds": 1,
    "knowledge_top_k": 4,
    "case_top_k": 3,
    "include_trace": false,
    "generate_answer": false
  }'
```

返回值包含 `evidence`（带编号和来源的证据）、`sufficient`（证据是否充分）、`missing_data`（待补数据）、`conflicts`（冲突）和 `coverage`（证据覆盖情况）。设置 `generate_answer: true` 时，额外返回 `answer` 和 `sources`。

多智能体接入示例见[Agentic RAG接入配置](res/Agentic_RAG多智能体系统接入配置.md)；完整字段见[接口文档](https://rag.orlando.ink/docs)。

---

## 本地开发

### 环境要求

```bash
conda activate PowerScopeRAG
```

### 添加新文档

将原始文件（`.pdf` / `.docx` / `.txt` / `.md`）放入对应的 inbox 目录：

```
doc/inbox/regulations/   ← 行业标准、技术导则
doc/inbox/paper/         ← 学术论文
```

然后运行转换脚本，自动输出为 Markdown：

```bash
# 增量转换（跳过已转换的文件）
python pipeline/convert.py

# 强制重新转换全部
python pipeline/convert.py --force

# 转换单个文件
python pipeline/convert.py --file doc/inbox/regulations/GB50054.pdf
```

支持格式：`.pdf`（pymupdf4llm）、`.docx`（python-docx）、`.txt`（自动识别编号标题）、`.md`（直接复制）。

### 构建历史案例库

`doc/cases/raw/` 保存案例与停电事件的原始资料，不进入通用知识文档转换流程。其中 `配网故障分析.docx` 按三级原因编号拆成独立案例，`停电分析0609.xlsx` 保留为原始停电分析数据。每条结构化案例包含触发条件、观测现象、停电范围、最终原因、处置过程和核验状态。

```bash
# 1. 解析 DOCX；未核验案例不会进入评测集
python pipeline/case_ingest.py --export-eval

# 2. 构建独立案例向量集合 powerscoperag_cases
python pipeline/embed.py --cases --reset

# 3. 同时查看主知识库证据和相似案例
python pipeline/retrieve.py "高温期间配变过载保护跳闸" 5 "" with-cases
```

专家确认案例后，在 `data/cases.jsonl` 中将对应记录的 `verification_status` 设为 `已确认`，并将 `evaluation_eligible` 设为 `true`，再单独导出评测数据：

```bash
python pipeline/case_ingest.py --eval-only
```

评测输入 `diagnostic_input` 只包含事件条件、观测量和停电范围，不包含最终原因与处置结果。导出的每个样本还带有 `exclude_case_ids`，评测检索时必须排除当前案例，防止通过检索原案例直接得到答案。

### 重新构建知识库

```bash
# 0. 格式转换（有新文档时执行）
python pipeline/convert.py

# 1. 清洗文档
python pipeline/clean.py

# 2. 语义分块
python pipeline/chunk.py

# 3. 向量化并写入向量库
python pipeline/embed.py

# 重新索引（清空后重建）
python pipeline/embed.py --reset
```

### 标题与层级感知检索

增强索引将文档标题、作者、文档类型和 H1/H2/H3 章节路径与正文共同用于向量化、BM25 和交叉编码精排。层级模式进一步聚合文档与章节得分，并根据文档置信度自适应选择 2 至 4 个同源互补片段，同时过滤近重复内容。API 默认使用 `hybrid+rerank+hierarchical`；可通过 `POWERSCOPE_RETRIEVAL_MODE=hybrid+rerank` 回滚，原索引和行为保持不变。

```bash
# 构建独立增强索引，不覆盖现有基线 collection
python pipeline/embed.py --structured --reset

# 仅验证标题与结构参与检索
python tests/eval.py --mode hybrid+rerank+structured --top-k 5

# 叠加文档/章节聚合与自适应证据选择
python tests/eval.py --mode hybrid+rerank+hierarchical --top-k 5
```

### Doc2Query 扩展索引（可选）

Doc2Query 用于离线生成“每个 chunk 可能回答的问题”，缓解用户查询与标准条文/论文陈述句之间的表达差异。生成阶段会做基础质量过滤，并可通过全库检索回测过滤泛化、重复或指向性不强的问题。

```bash
# 1. 生成候选问题 + 标准答案，并做全库回测过滤（默认使用 hybrid 快速回测）
python pipeline/doc2query.py --resume

# 调试时可先只跑少量 chunk
python pipeline/doc2query.py --limit 20 --resume

# 如需更严格但更慢的回测，可抽样使用 rerank
python pipeline/doc2query.py --limit 20 --resume --force --backcheck-mode hybrid+rerank

# 2. 构建 doc2query 专用向量索引
python pipeline/embed.py --doc2query --reset

# 3. 使用扩展索引评测
python tests/eval.py --mode hybrid+rerank+doc2query --top-k 5
```

输出文件：

| 文件 | 说明 |
|------|------|
| `data/doc2query.jsonl` | 每个 chunk 的生成问题、标准答案、过滤原因和回测结果 |
| `data/chunks_doc2query.jsonl` | 带 `indexed_content` 的扩展 chunks，用于构建 doc2query 索引 |

### 检索质量评测

```bash
# 评测全部 200 题
python tests/eval.py

# 只测指定题目
python tests/eval.py --ids Q01 Q06 Q07

# 调整检索数量
python tests/eval.py --top-k 10
```

评测报告保存在 `tests/reports/report_YYYYMMDD_HHMMSS.md`。

### 本地启动 API

```bash
uvicorn api:app --host "::" --port 8000
```

### 命令行检索测试

```bash
python pipeline/retrieve.py "供电电压偏差限值" 5
python pipeline/retrieve.py "台区低电压原因" 5 journal
```

---

## 服务管理

服务由 systemd 管理，开机自启，崩溃自动重启。

```bash
# 查看状态
systemctl status powerscoperag

# 查看实时日志
journalctl -u powerscoperag -f

# 重启服务
systemctl restart powerscoperag

# 停止服务
systemctl stop powerscoperag
```
