---
id: open-eval-pyodps-create-partition-explicit
title: pyodps requires explicit partition creation before writing
triggers:
  - pyodps
  - partition
  - create_partition
  - open_writer
  - ODPS
  - write
applies_to:
  - runtime/
  - backend/ (ODPS integration)
staleness: stable
source:
  - user feedback 2026-06-23
---

## Rule

pyodps `table.open_writer(partition=...)` **does NOT auto-create partitions**. You must explicitly call `table.create_partition(spec, if_not_exists=True)` before writing.

## Why It Matters

When switching to two-level partitions (task_id/inst_id), each new instance creates a new partition. First write to a new partition will fail with obscure error if `create_partition` wasn't called. Unit tests mock this away — only end-to-end execution exposes the bug.

## Correct Pattern

```python
from odps import PartitionSpec

# 1. Create partition explicitly
spec = PartitionSpec("task_id=132,inst_id=311")
table.create_partition(spec, if_not_exists=True)

# 2. Now safe to write
with table.open_writer(partition=spec) as writer:
    writer.write(data)
```

## Wrong Pattern

```python
# This will fail if partition doesn't exist
with table.open_writer(partition="task_id=132,inst_id=311") as writer:
    writer.write(data)  # ERROR: partition not found
```

## Verify

```python
# Check if partition exists
exists = table.exist_partition(PartitionSpec("task_id=132,inst_id=311"))
print(f"Partition exists: {exists}")

# Create if not exists
table.create_partition(
    PartitionSpec("task_id=132,inst_id=311"),
    if_not_exists=True
)
```

## Caution

- `if_not_exists=True` is idempotent — safe to call even if partition exists
- Forgetting `create_partition` is the #1 cause of "write works locally but fails in production" for ODPS
- Always pair `open_writer` with preceding `create_partition` in the same code path
