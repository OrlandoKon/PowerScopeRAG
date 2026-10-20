"""Show progress and running averages for the current RAGAS evaluation."""

import json
from pathlib import Path

from ragas_eval import _row_fingerprint


ROOT = Path(__file__).resolve().parent.parent
MODE = "hybrid_rerank_hierarchical"
PIPELINE_CACHE = ROOT / "tests" / f"pipeline_cache_{MODE}.json"
SCORE_CACHE = ROOT / "tests" / f"ragas_score_cache_{MODE}.json"
METRICS = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "answer_correctness",
)


def main() -> None:
    pipeline = json.loads(PIPELINE_CACHE.read_text(encoding="utf-8"))
    cache = json.loads(SCORE_CACHE.read_text(encoding="utf-8"))
    current = []
    completed_ids = []
    for row in pipeline:
        item = cache.get(row["id"], {})
        if item.get("fingerprint") != _row_fingerprint(row):
            continue
        scores = item.get("scores")
        if not scores:
            continue
        current.append(scores)
        completed_ids.append(row["id"])

    total = len(pipeline)
    count = len(current)
    last_id = completed_ids[-1] if completed_ids else "-"
    print(f"RAGAS progress: {count}/{total} ({count / total:.1%}), last={last_id}")
    if not current:
        return
    for metric in METRICS:
        mean = sum(float(row[metric]) for row in current) / count
        print(f"{metric:20s}: {mean:.4f}")


if __name__ == "__main__":
    main()
