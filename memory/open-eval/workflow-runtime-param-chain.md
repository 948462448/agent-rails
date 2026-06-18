---
id: open-eval-workflow-runtime-param-chain
title: Trace workflow parameters end to end
triggers:
  - dolphin
  - runtime
  - ray
  - evalType
  - workflow
  - rawScripts
applies_to:
  - backend/
  - dolphin/
  - runtime/
staleness: verify-first
source:
  - AGENTS.md
  - runtime/AGENTS.md
---

## Rule

For evaluation workflow changes, trace parameter shape across the whole chain instead of reviewing each file locally:

```text
backend AppService / gateway
  -> Dolphin workflow scripts
  -> Ray entrypoint
  -> runtime adapter
  -> result persistence / report
```

## Why It Matters

OpenEval has several cross-component seams. A field can look valid in backend code but still be absent, renamed, or defaulted differently in Dolphin / Ray / runtime.

## Verify

- Check producer and consumer field names side by side.
- For Ray Job launch params such as `*_CODE_URI` and `*_REQUIREMENTS_JSON`, verify the value is non-empty before DS starts or the backend fails fast with a clear error.
- If backend code inlines a Dolphin rawScript while a `dolphin/**/rawscript/*.py` file claims to be source of truth, compare the two or document which one is canonical.
- Add or run the closest backend / runtime unit tests.
- For Python entrypoints touched under `dolphin/`, run `py_compile` at minimum.

```bash
python3 -m py_compile dolphin/tpp_eval_node/rawscript/tpp_eval_dolphin_main.py dolphin/tpp_eval_node/ray_entry/tpp_eval_main.py
```
