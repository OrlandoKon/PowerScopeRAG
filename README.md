# PowerScopeRAG

面向雄安新区 400V 低压配电网停电原因分析的 RAG 知识库检索服务。

---

## 知识库内容

| 类别 | 数量 | 说明 |
|------|------|------|
| 行业标准（regulation） | 6 份 | GB/T 12325、GB/T 15543、GB 50054、GB 50217、GBT 13955、DL/T 2806 等 |
| 期刊论文（journal） | 9 份 | 台区低电压、分布式光伏、储能系统相关研究论文 |
| **合计** | **665 个分块** | 向量化存储，支持语义检索 |

---

## API 地址

```
http://rag.orlando.ink:8000
```

---

## 接口说明

| 接口 | 方法 | 功能 |
|------|------|------|
| `/` | GET | 健康检查 |
| `/stats` | GET | 向量库统计 |
| `/retrieve` | POST | 语义检索，返回原始片段 |
| `/ask` | POST | **RAG 问答**，检索 + DeepSeek 生成回答 |

---

### GET `/`  健康检查

```bash
curl http://rag.orlando.ink:8000/
```

返回：
```json
{"status": "ok", "service": "PowerScopeRAG Retrieval API"}
```

---

### GET `/stats`  向量库统计

```bash
curl http://rag.orlando.ink:8000/stats
```

返回：
```json
{
  "total_documents": 665,
  "collection": "powerscoperag",
  "model": "BAAI/bge-large-zh-v1.5",
  "vectorstore_path": "..."
}
```

---

### POST `/retrieve`  语义检索

输入问题，返回知识库中最相关的原始文本片段，不调用 LLM。

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | ✅ | 检索问题，中英文均可，最长 500 字符 |
| `top_k` | int | ❌ | 返回结果数量，默认 5，范围 1–20 |
| `doc_type` | string | ❌ | 文档类型过滤，不填则检索全部 |

**返回字段（`results` 数组中每条）：**

| 字段 | 说明 |
|------|------|
| `content` | 检索到的文本片段 |
| `source` | 来源文件名 |
| `doc_type` | 文档类型 |
| `H1` / `H2` / `H3` | 该片段所在章节标题（可能为空） |
| `chunk_idx` | 该片段在源文档中的位置序号 |
| `distance` | 余弦距离，**越小越相关** |

---

### POST `/ask`  RAG 问答（推荐）

检索相关片段后交给 DeepSeek 生成回答，返回最终答案和参考来源。

**请求体**与 `/retrieve` 完全相同。

**返回字段：**

| 字段 | 说明 |
|------|------|
| `query` | 原始问题 |
| `answer` | DeepSeek 根据知识库生成的回答 |
| `sources` | 参考片段列表（同 `/retrieve` 的 `results`） |

---

## 使用示例

### 示例 1：RAG 问答

```bash
curl -X POST http://rag.orlando.ink:8000/ask \
  -H "Content-Type: application/json" \
  -d '{
    "query": "20kV以下三相供电电压偏差限值是多少？",
    "top_k": 4,
    "doc_type": "regulation"
  }'
```

返回（节选）：
```json
{
  "query": "20kV以下三相供电电压偏差限值是多少？",
  "answer": "根据《GB/T 12325-2008》第4.2节，20 kV及以下三相供电电压偏差的限值为标称电压的 ±7%。",
  "sources": [
    {
      "content": "4.2  20 kV及以下三相供电电压偏差为标称电压的 ±7%。...",
      "source": "12325-2008-gbt-e-300.md",
      "doc_type": "regulation",
      "H1": "4 供电电压偏差的限值",
      "H2": null,
      "H3": null,
      "chunk_idx": 8,
      "distance": 0.364
    }
  ]
}
```

---

### 示例 2：只检索不生成（调试用）

```bash
curl -X POST http://rag.orlando.ink:8000/retrieve \
  -H "Content-Type: application/json" \
  -d '{
    "query": "台区低电压的主要原因有哪些",
    "top_k": 5,
    "doc_type": "journal"
  }'
```

---

### 示例 3：Python 调用

```python
import requests

# RAG 问答
resp = requests.post(
    "http://rag.orlando.ink:8000/ask",
    json={"query": "三相电压不平衡度限值是多少？", "top_k": 5}
)
data = resp.json()
print(data["answer"])
for src in data["sources"]:
    print(f"  [{src['distance']:.3f}] {src['source']} | {src.get('H1', '')}")
```

---

### 示例 4：`doc_type` 可选值

| 值 | 含义 |
|----|------|
| `regulation` | 只检索行业标准（GB/T、DL/T 等） |
| `journal` | 只检索期刊论文 |
| `thesis` | 只检索学位论文 |
| 不填 | 检索全部文档 |

---

### 示例 5：交互式文档（浏览器）

在浏览器中打开：

```
http://rag.orlando.ink:8000/docs
```

页面列出所有接口，点击任意接口 → **Try it out** → 填参数 → **Execute**，结果直接显示在页面上，不需要写任何代码。

---

## 本地开发

### 环境要求

```bash
conda activate PowerScopeRAG
```

### 重新构建知识库（从原始文档开始）

```bash
# 1. 清洗文档
python pipeline/clean.py

# 2. 语义分块
python pipeline/chunk.py

# 3. 向量化并写入向量库
python pipeline/embed.py

# 重新索引（清空后重建）
python pipeline/embed.py --reset
```

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
