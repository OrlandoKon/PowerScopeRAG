一、整体 Pipeline 架构

doc/regulations/ + doc/paper/
        │
        ▼
  ┌─────────────┐
  │  1. 清洗层   │  document-type-aware 清洗
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │  2. 切片层   │  MarkdownHeaderTextSplitter + RecursiveCharacterTextSplitter
  └──────┬──────┘
         │  chunks + metadata
  ┌──────▼──────┐
  │  3. 嵌入层   │  中文技术嵌入模型
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │  4. 索引层   │  向量数据库 + 可选 BM25 索引
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │  5. 检索层   │  hybrid search + rerank
  └─────────────┘
每层独立可重跑，不要做成一个长脚本。

二、清洗层——两类文档的处理差异
这是最容易被低估的步骤，但对检索质量影响最大。

regulations/ 标准文档的典型噪声：

文档头部的行政信息（ICS 分类号、CCS 号、发布/实施日期、归口单位）→ 检索价值为零，直接丢弃
前言 章节中"本标准按照 GB/T 1.1-2020 起草"类的套话 → 丢弃
规范性引用文件 章节（仅列标准名称）→ 可保留作 metadata 但不参与检索
术语和定义 → 高价值，必须保留，这是 LLM 最容易答错的部分
paper/ 学术论文的典型噪声：

参考文献 节尾部（[1] 作者, 题目, 期刊, 年份）→ 对停电分析无检索价值，丢弃
致谢 → 丢弃
图表说明（图1 xxx 但没有实际图片）→ caption 本身可保留，但需要标记"无图"
共性问题：

DOCX 转换后可能出现孤立的短行（如仅含标准号或页码）→ 设长度阈值过滤（< 20字的孤立段落）
数学公式转换不完整，留下残缺符号 → 决策：保留文本上下文，容忍公式不可读
三、切片策略——两阶段方案
推荐：两级切片

第一级用 MarkdownHeaderTextSplitter，按标题层级切分，同时把父级标题链注入 metadata：


chunk.metadata = {
  "H1": "第4章 技术要求",
  "H2": "4.2 供电电压偏差限值",
  "source": "12325-2008-gbt-e-300.md",
  "doc_type": "regulation"
}
第二级用 RecursiveCharacterTextSplitter 把超长节再拆，chunk_size 建议 400-600 中文字符（等价约 500-750 token），overlap 80-100 字符。

为什么不直接用 RecursiveCharacterTextSplitter：
标准文档中"4.2.1 限值为±7%" 这一句如果和上下文分离，LLM 无法知道这是哪个标准的哪个条款。父标题链作为 metadata 传递，检索时可以附在 prompt 里补全上下文。

两类文档切片粒度不同：

文档类型	切片粒度	原因
regulations/	## 条款级（精细）	标准的核心价值在具体条文，条文之间相对独立
paper/	# 或 ## 章节级（粗一些）	论文的推理链需要段落间上下文，粒度太细会丢论据
四、嵌入模型选型
这是最关键的决策，因为嵌入模型确定后很难更换（需要重建整个索引）。

核心需求： 中文技术术语检索，如"三相不平衡度"、"漏电保护器"、"重合闸"等专有名词的语义识别。

候选	优点	缺点
BAAI/bge-large-zh-v1.5	中文检索 SOTA，本地运行，无调用费	需要 GPU 或接受慢速 CPU 推理
text-embedding-3-large（OpenAI）	效果强，API 简单	付费，数据出境
阿里云百炼 text-embedding-v3	与百炼平台已有生态一致	绑定阿里云，API 依赖
DeepSeek	无独立嵌入模型	不适用
推荐：bge-large-zh-v1.5 本地运行，理由：

中文技术文档检索效果最佳
数据不出境（电网数据合规考量）
后期可切换到 bge-m3（多语言+稀疏+密集混合）
五、向量数据库选型
当前规模：16 个文档，预估 800-1500 个 chunks，体量很小。

推荐：Chroma（本地），够用，简单，后期可迁移 Milvus。不要因为"生产需要用 Milvus"就在原型阶段引入复杂性。

关键考量——是否需要 Hybrid Search（向量 + BM25）：

这个场景强烈建议上。原因：停电分析的检索 query 中会出现大量精确名词：

"GB/T 12325" 这种标准号，嵌入模型语义相似性不可靠
"±7%" ">85°C" 这类阈值数值，必须精确匹配
"漏电保护器 RCD" 这类术语别名
纯向量检索会漏掉这些，加 BM25 稀疏检索做融合（RRF 算法）能显著提升召回。LangChain 有 EnsembleRetriever 可以直接组合。

六、项目目录规划建议

PowerScopeRAG/
├── doc/                    # 知识库原始文档（现有）
│   ├── regulations/
│   └── paper/
├── res/                    # 业务框架文档（现有）
├── pipeline/               # RAG 流水线（待建）
│   ├── clean.py            # 清洗：输入 doc/ md，输出 cleaned/ md
│   ├── chunk.py            # 切片：输入 cleaned/，输出 chunks jsonl
│   ├── embed.py            # 嵌入：输入 chunks jsonl，写入向量库
│   └── retrieve.py         # 检索接口：封装 hybrid search + rerank
├── data/                   # 中间产物（不进 git）
│   ├── cleaned/            # 清洗后的 md
│   ├── chunks.jsonl        # 切片结果（含 metadata）
│   └── vectorstore/        # Chroma 持久化目录
├── config/
│   └── pipeline.yaml       # chunk_size, overlap, embedding_model 等
├── agents/                 # 多智能体系统（后续）
└── WeekReport/
七、与多智能体系统的接口
retrieve.py 是这个 pipeline 和 agents/ 之间的边界。它对外暴露一个函数：


def retrieve(query: str, doc_type: str = None, top_k: int = 5) -> list[dict]:
    # 返回: [{"content": "...", "source": "GB 50054", "section": "7.2.1", "score": 0.87}]
doc_type 参数允许 agent 指定只检索标准还是论文，实现定向召回。这一设计让向量库对 agent 层透明——agent 不需要知道底层是 Chroma 还是 Milvus。

优先级建议：

先做清洗和切片，人工抽查 50 个 chunks 的质量
嵌入 + 建索引
用周报里计划的 20 个测试问题验证检索 recall（不用 LLM，只看检索结果是否命中）
检索通过后再接 DeepSeek 生成层