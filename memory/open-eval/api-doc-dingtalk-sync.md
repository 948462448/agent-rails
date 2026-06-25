---
id: open-eval-api-doc-dingtalk-sync
title: Backend API shape changes must sync to DingTalk API doc
triggers:
  - backend api
  - contracts
  - DingTalk
  - alidocs
  - API doc
applies_to:
  - contracts/
  - backend/
staleness: stable
source:
  - AGENTS.md
  - backend/AGENTS.md
---

## Rule

When backend REST API shape changes (paths, request/response), in addition to updating `contracts/backend-api.yaml` and running `make codegen`, also sync the DingTalk API document:

- URL: `https://alidocs.dingtalk.com/i/nodes/R1zknDm0WR6XzZ4LtxNnv0mnWBQEx5rG`
- Title: 评测任务 API 文档（v2 修订版 · 第一期 · 基础模式）
- Sections: 评测任务 / 归属项目 / 裁判模型 / 裁判指标 / 实例 / 评测报告 / 变更记录 / 选项元数据 + 接口清单总表 + 汇总数字

## Why It Matters

The DingTalk doc is the external-facing API contract for consumers. If it drifts from the implementation, downstream integrations break silently.

## How To Sync

Use the `dingtalk-doc-rw` skill (not a browser):

```python
from scripts.dingtalk_doc import read_doc, update_doc
url = "https://alidocs.dingtalk.com/i/nodes/R1zknDm0WR6XzZ4LtxNnv0mnWBQEx5rG"
md = read_doc(url=url)["markdown"]
# patch md ...
update_doc(url=url, content=md)  # full overwrite, not patch
```

## Verify

After sync, read the doc back and confirm the changed section matches the new API shape.

## Caution

`update_doc` is overwrite-style (read → patch → write). Always `read_doc` first to get the latest version. DingTalk markdown escapes `+` as `\+`.
