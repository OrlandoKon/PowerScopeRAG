#!/usr/bin/env bash
set -euo pipefail

unit=powerscoperag-agentic200-refined.service
state=active
for ((attempt=0; attempt<720; attempt++)); do
    state=$(systemctl show "$unit" -p ActiveState --value)
    if [[ "$state" == inactive || "$state" == failed ]]; then
        break
    fi
    sleep 30
done

status=$(systemctl show "$unit" -p ExecMainStatus --value)
if [[ "$state" != inactive || "$status" != 0 ]]; then
    printf 'Agentic refined evaluation did not finish successfully: state=%s status=%s\n' "$state" "$status" >&2
    exit 1
fi

scores=/root/Repository/PowerScopeRAG/tests/agentic_200_ragas_agentic_refined_v2.json
if ! jq -e 'length == 200 and all(.[]; (.scores | [.faithfulness, .answer_relevancy, .context_precision, .context_recall, .answer_correctness] | all(. != null)))' "$scores" >/dev/null; then
    printf 'Agentic refined evaluation has missing RAGAS scores; Bailian comparison has not started.\n' >&2
    exit 1
fi

exec /root/.local/share/mamba/envs/PowerScopeRAG/bin/python -u \
    /root/Repository/PowerScopeRAG/tests/agentic_200_eval.py \
    --phase bailian-finish
