---
id: open-eval-pyodps-partition-spec-comma
title: pyodps PartitionSpec uses comma not slash for two-level partitions
triggers:
  - pyodps
  - partition
  - PartitionSpec
  - ODPS
  - two-level
applies_to:
  - runtime/
  - backend/ (ODPS integration)
staleness: stable
source:
  - user feedback 2026-06-23
---

## Rule

pyodps `PartitionSpec` string form requires **comma** separator: `task_id=X,inst_id=Y`

SQL/directory style uses **slash**: `task_id=X/inst_id=Y`

**Mixing slash into PartitionSpec throws ValueError** that unit tests cannot mock — only surfaces in end-to-end execution.

## Why It Matters

This is a silent trap: code looks correct in isolation, tests pass, but runtime execution fails with cryptic ValueError from pyodps internals.

## Examples

```python
# Correct
spec = PartitionSpec("task_id=132,inst_id=311")  # comma
spec = PartitionSpec(task_id=132, inst_id=311)   # kwargs

# Wrong - throws ValueError
spec = PartitionSpec("task_id=132/inst_id=311")  # slash
```

## Verify

```python
from odps import PartitionSpec

# Should work
spec = PartitionSpec("task_id=132,inst_id=311")
print(spec)

# Should raise ValueError
try:
    PartitionSpec("task_id=132/inst_id=311")
except ValueError as e:
    print(f"Caught expected error: {e}")
```

## Caution

Never use slash-separated partition strings with pyodps PartitionSpec. If you see partition specs in SQL or file paths (slash format), convert to comma format before passing to pyodps APIs.
