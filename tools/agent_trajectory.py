#!/usr/bin/env python3
"""Normalize coding-agent exports into a local run IR, OTLP JSON, and ATIF."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


RUN_IR_SCHEMA = "agent-eval-run/v1"
ATIF_SCHEMA = "ATIF-v1.7"
OTEL_SCHEMA_URL = "https://opentelemetry.io/schemas/gen-ai/1.42.0"
ADAPTER_VERSION = "0.1.0"


class TrajectoryError(RuntimeError):
    """A source export cannot be converted without guessing required data."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def read_text(path: Path, max_chars: int) -> str:
    if not path.is_file():
        raise TrajectoryError(f"source file not found: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        raise TrajectoryError(f"source exceeds --max-chars: {path}")
    return text


def atomic_write_text(path: Path, text: str, force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    if path.exists() and not force:
        raise TrajectoryError(f"output already exists: {path}; pass --force to replace it")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, value: Any, force: bool) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n", force)


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return compact_json(value)


def number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return value


def to_unix_nano(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        if value >= 100_000_000_000:
            return int(value * 1_000_000)
        return int(value * 1_000_000_000)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1_000_000_000)
    return None


def nano_to_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def timing(start: Any = None, end: Any = None) -> Optional[dict[str, Any]]:
    start_nano = to_unix_nano(start)
    end_nano = to_unix_nano(end)
    if start_nano is None:
        return None
    if end_nano is None or end_nano < start_nano:
        end_nano = start_nano
    return {
        "start_unix_nano": str(start_nano),
        "end_unix_nano": str(end_nano),
        "observed": True,
    }


def source_timestamp(value: dict[str, Any]) -> Any:
    return value.get("timestamp") or value.get("time") or value.get("created_at")


def normalized_tool(
    call_id: str,
    name: str,
    arguments: Any,
    result: Any,
    status: str,
    extra: Optional[dict[str, Any]] = None,
    tool_timing: Optional[dict[str, Any]] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    arguments_value = arguments if isinstance(arguments, dict) else {"value": arguments}
    call: dict[str, Any] = {
        "tool_call_id": call_id,
        "function_name": name,
        "arguments": arguments_value,
    }
    observation: dict[str, Any] = {
        "source_call_id": call_id,
        "content": as_text(result),
        "status": status,
    }
    if extra:
        observation["extra"] = extra
    if tool_timing:
        call["timing"] = tool_timing
    return call, observation


def codex_tool(item: dict[str, Any]) -> Optional[tuple[dict[str, Any], dict[str, Any]]]:
    item_type = item.get("type")
    call_id = str(item.get("id") or f"codex-{item_type or 'tool'}")
    status = str(item.get("status") or "completed")
    if item_type == "command_execution":
        extra = {"exit_code": item.get("exit_code")} if item.get("exit_code") is not None else None
        return normalized_tool(
            call_id,
            "shell",
            {"command": item.get("command", "")},
            item.get("aggregated_output", item.get("output", "")),
            status,
            extra,
        )
    if item_type == "mcp_tool_call":
        server = str(item.get("server") or "mcp")
        tool = str(item.get("tool") or item.get("name") or "tool")
        result = item.get("result")
        if result is None:
            result = item.get("error", "")
        return normalized_tool(
            call_id,
            f"{server}.{tool}",
            item.get("arguments", {}),
            result,
            status,
        )
    if item_type == "web_search":
        return normalized_tool(
            call_id,
            "web_search",
            {"query": item.get("query", "")},
            item.get("result", status),
            status,
        )
    if item_type == "file_change":
        return normalized_tool(
            call_id,
            "file_change",
            {"changes": item.get("changes", [])},
            item.get("result", status),
            status,
        )
    return None


def codex_metrics(usage: dict[str, Any]) -> Optional[dict[str, Any]]:
    prompt = number(usage.get("input_tokens"))
    completion = number(usage.get("output_tokens"))
    cached = number(usage.get("cached_input_tokens"))
    reasoning = number(usage.get("reasoning_output_tokens"))
    if prompt is None and completion is None and cached is None and reasoning is None:
        return None
    metrics: dict[str, Any] = {}
    if prompt is not None:
        metrics["prompt_tokens"] = prompt
    if completion is not None:
        metrics["completion_tokens"] = completion
    if cached is not None:
        metrics["cached_tokens"] = cached
    extra: dict[str, Any] = {"usage_scope": "turn"}
    if reasoning is not None:
        extra["reasoning_tokens"] = reasoning
    if number(usage.get("total_tokens")) is not None:
        extra["source_total_tokens"] = usage["total_tokens"]
    metrics["extra"] = extra
    return metrics


def parse_codex(
    source_text: str,
    task_text: str,
    session_override: Optional[str],
    agent_name: str,
    agent_version: str,
    model: str,
    provider: Optional[str],
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(source_text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise TrajectoryError(f"Codex JSONL line {line_number} is invalid JSON") from error
        if not isinstance(value, dict):
            raise TrajectoryError(f"Codex JSONL line {line_number} must be an object")
        events.append(value)
    if not events:
        raise TrajectoryError("Codex JSONL is empty")

    session_id = session_override
    for event in events:
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
            session_id = session_id or event["thread_id"]
            break
    if not session_id:
        raise TrajectoryError("Codex JSONL has no thread_id; pass --session-id")

    steps: list[dict[str, Any]] = [
        {"sequence": 1, "source": "user", "message": task_text, "extra": {"source": "task-file"}}
    ]
    pending_reasoning: list[str] = []
    turn_agent_indexes: list[int] = []
    unmapped: set[str] = set()
    for event in events:
        event_type = event.get("type")
        if event_type == "turn.started":
            turn_agent_indexes = []
            continue
        if event_type == "item.completed":
            item = as_dict(event.get("item"))
            item_type = str(item.get("type") or "unknown")
            event_timing = timing(source_timestamp(event))
            if item_type == "reasoning":
                text = as_text(item.get("text"))
                if text:
                    pending_reasoning.append(text)
                continue
            if item_type == "agent_message":
                step: dict[str, Any] = {
                    "sequence": len(steps) + 1,
                    "source": "agent",
                    "message": as_text(item.get("text")),
                    "model_name": model,
                    "extra": {"source_item_type": item_type, "source_item_id": item.get("id")},
                }
                if pending_reasoning:
                    step["reasoning_content"] = "\n\n".join(pending_reasoning)
                    pending_reasoning = []
                if event_timing:
                    step["timing"] = event_timing
                steps.append(step)
                turn_agent_indexes.append(len(steps) - 1)
                continue
            tool = codex_tool(item)
            if tool:
                call, observation = tool
                step = {
                    "sequence": len(steps) + 1,
                    "source": "agent",
                    "message": "",
                    "model_name": model,
                    "tool_calls": [call],
                    "observations": [observation],
                    "extra": {
                        "source_item_type": item_type,
                        "source_item_id": item.get("id"),
                        "llm_boundary": "unknown",
                    },
                }
                if pending_reasoning:
                    step["reasoning_content"] = "\n\n".join(pending_reasoning)
                    pending_reasoning = []
                if event_timing:
                    step["timing"] = event_timing
                steps.append(step)
                turn_agent_indexes.append(len(steps) - 1)
                continue
            unmapped.add(item_type)
            continue
        if event_type == "turn.completed":
            metrics = codex_metrics(as_dict(event.get("usage")))
            if metrics:
                if turn_agent_indexes:
                    target = steps[turn_agent_indexes[-1]]
                else:
                    target = {
                        "sequence": len(steps) + 1,
                        "source": "agent",
                        "message": "",
                        "model_name": model,
                        "extra": {"source_event_type": event_type},
                    }
                    steps.append(target)
                target["metrics"] = metrics
            continue
        if event_type in ("turn.failed", "error"):
            error = as_dict(event.get("error"))
            steps.append(
                {
                    "sequence": len(steps) + 1,
                    "source": "system",
                    "message": f"Agent turn failed: {as_text(error.get('message') or event.get('message'))}",
                    "extra": {"error": True, "source_event_type": event_type},
                }
            )

    if pending_reasoning:
        steps.append(
            {
                "sequence": len(steps) + 1,
                "source": "agent",
                "message": "",
                "model_name": model,
                "reasoning_content": "\n\n".join(pending_reasoning),
                "extra": {"llm_boundary": "unknown"},
            }
        )
    return {
        "session_id": session_id,
        "agent": {
            "name": agent_name,
            "version": agent_version,
            "model_name": model,
            "provider_name": provider,
        },
        "steps": steps,
        "fidelity": {
            "messages": "task-file-plus-codex-items",
            "llm_boundaries": "turn-level-usage-only",
            "unmapped_source_item_types": sorted(unmapped),
        },
    }


def opencode_metrics(info: dict[str, Any]) -> Optional[dict[str, Any]]:
    tokens = as_dict(info.get("tokens"))
    cache = as_dict(tokens.get("cache"))
    input_tokens = number(tokens.get("input"))
    output_tokens = number(tokens.get("output"))
    reasoning_tokens = number(tokens.get("reasoning"))
    cache_read = number(cache.get("read"))
    cache_write = number(cache.get("write"))
    cost = number(info.get("cost"))
    if all(value is None for value in (input_tokens, output_tokens, reasoning_tokens, cache_read, cache_write, cost)):
        return None
    metrics: dict[str, Any] = {}
    if input_tokens is not None or cache_read is not None or cache_write is not None:
        metrics["prompt_tokens"] = (input_tokens or 0) + (cache_read or 0) + (cache_write or 0)
    if output_tokens is not None or reasoning_tokens is not None:
        metrics["completion_tokens"] = (output_tokens or 0) + (reasoning_tokens or 0)
    if cache_read is not None:
        metrics["cached_tokens"] = cache_read
    if cost is not None:
        metrics["cost_usd"] = cost
    metrics["extra"] = {
        "source_input_tokens": input_tokens,
        "source_output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "cache_write_tokens": cache_write,
        "source_total_tokens": number(tokens.get("total")),
        "accounting": "prompt=input+cache_read+cache_write; completion=output+reasoning",
    }
    return metrics


def parse_opencode(
    source_text: str,
    task_text: Optional[str],
    session_override: Optional[str],
    agent_name: str,
    agent_version: str,
    model_override: Optional[str],
    provider_override: Optional[str],
) -> dict[str, Any]:
    try:
        export = json.loads(source_text)
    except json.JSONDecodeError as error:
        raise TrajectoryError("OpenCode export is not valid JSON") from error
    if not isinstance(export, dict):
        raise TrajectoryError("OpenCode export must be an object")
    session_info = as_dict(export.get("info"))
    session_id = session_override or session_info.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise TrajectoryError("OpenCode export has no session id; pass --session-id")
    messages = as_list(export.get("messages"))
    if not messages:
        raise TrajectoryError("OpenCode export has no messages")

    inferred_model: Optional[str] = None
    inferred_provider: Optional[str] = None
    steps: list[dict[str, Any]] = []
    last_system: Optional[str] = None
    file_parts = 0
    for message in messages:
        value = as_dict(message)
        info = as_dict(value.get("info"))
        parts = [as_dict(part) for part in as_list(value.get("parts"))]
        role = info.get("role")
        message_time = as_dict(info.get("time"))
        message_timing = timing(message_time.get("created"), message_time.get("completed"))
        if role == "user":
            system = info.get("system")
            if isinstance(system, str) and system and system != last_system:
                system_step: dict[str, Any] = {
                    "sequence": len(steps) + 1,
                    "source": "system",
                    "message": system,
                    "extra": {"source_message_id": info.get("id")},
                }
                if message_timing:
                    system_step["timing"] = message_timing
                steps.append(system_step)
                last_system = system
            text_parts: list[str] = []
            for part in parts:
                if part.get("type") == "text" and not part.get("ignored"):
                    text_parts.append(as_text(part.get("text")))
                elif part.get("type") == "file":
                    file_parts += 1
                    text_parts.append(
                        f"[file:{as_text(part.get('mime'))}:{as_text(part.get('filename') or part.get('id'))}]"
                    )
                elif part.get("type") == "subtask":
                    text_parts.append(as_text(part.get("prompt")))
            step = {
                "sequence": len(steps) + 1,
                "source": "user",
                "message": "\n\n".join(part for part in text_parts if part),
                "extra": {"source_message_id": info.get("id")},
            }
            if message_timing:
                step["timing"] = message_timing
            steps.append(step)
            continue
        if role != "assistant":
            continue

        inferred_model = inferred_model or (str(info.get("modelID")) if info.get("modelID") else None)
        inferred_provider = inferred_provider or (
            str(info.get("providerID")) if info.get("providerID") else None
        )
        text_parts = []
        reasoning_parts = []
        tool_calls: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []
        for part in parts:
            part_type = part.get("type")
            if part_type == "text" and not part.get("ignored"):
                text_parts.append(as_text(part.get("text")))
            elif part_type == "reasoning":
                reasoning_parts.append(as_text(part.get("text")))
            elif part_type == "tool":
                state = as_dict(part.get("state"))
                state_status = str(state.get("status") or "unknown")
                tool_time = as_dict(state.get("time"))
                tool_timing = timing(tool_time.get("start"), tool_time.get("end"))
                result = state.get("output") if state_status == "completed" else state.get("error", "")
                call, observation = normalized_tool(
                    str(part.get("callID") or part.get("id") or "opencode-tool"),
                    str(part.get("tool") or "tool"),
                    state.get("input", {}),
                    result,
                    state_status,
                    {"title": state.get("title")} if state.get("title") is not None else None,
                    tool_timing,
                )
                tool_calls.append(call)
                observations.append(observation)
        model = model_override or inferred_model or "unknown"
        step = {
            "sequence": len(steps) + 1,
            "source": "agent",
            "message": "\n\n".join(part for part in text_parts if part),
            "model_name": model,
            "provider_name": provider_override or inferred_provider,
            "llm_call_count": 1,
            "extra": {
                "source_message_id": info.get("id"),
                "finish_reason": info.get("finish"),
                "error": info.get("error"),
            },
        }
        if reasoning_parts:
            step["reasoning_content"] = "\n\n".join(reasoning_parts)
        if tool_calls:
            step["tool_calls"] = tool_calls
            step["observations"] = observations
        metrics = opencode_metrics(info)
        if metrics:
            step["metrics"] = metrics
        if message_timing:
            step["timing"] = message_timing
        steps.append(step)

    if task_text and not any(step["source"] == "user" for step in steps):
        steps.insert(0, {"sequence": 1, "source": "user", "message": task_text, "extra": {"source": "task-file"}})
    for index, step in enumerate(steps, start=1):
        step["sequence"] = index
    model_name = model_override or inferred_model or "unknown"
    provider_name = provider_override or inferred_provider
    return {
        "session_id": session_id,
        "agent": {
            "name": agent_name,
            "version": agent_version,
            "model_name": model_name,
            "provider_name": provider_name,
        },
        "steps": steps,
        "fidelity": {
            "messages": "opencode-export",
            "llm_boundaries": "assistant-message",
            "file_parts": "metadata-only" if file_parts else "none",
        },
    }


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [clean(item) for item in value]
    return value


def timing_fidelity(steps: list[dict[str, Any]]) -> str:
    observed = sum(1 for step in steps if as_dict(step.get("timing")).get("observed") is True)
    if observed == len(steps):
        return "observed"
    if observed:
        return "partial"
    return "synthetic-order-only"


def build_atif(run: dict[str, Any]) -> dict[str, Any]:
    atif_steps: list[dict[str, Any]] = []
    prompt_total = 0.0
    completion_total = 0.0
    cached_total = 0.0
    cost_total = 0.0
    seen_prompt = seen_completion = seen_cached = seen_cost = False
    for step in run["steps"]:
        atif_step: dict[str, Any] = {
            "step_id": step["sequence"],
            "source": step["source"],
            "message": step.get("message", ""),
        }
        step_timing = as_dict(step.get("timing"))
        if step_timing.get("observed") is True:
            atif_step["timestamp"] = nano_to_iso(int(step_timing["start_unix_nano"]))
        if step["source"] == "agent":
            for field in ("model_name", "reasoning_content", "llm_call_count"):
                if step.get(field) is not None:
                    atif_step[field] = step[field]
            if step.get("tool_calls"):
                atif_step["tool_calls"] = [
                    clean(
                        {
                            "tool_call_id": call["tool_call_id"],
                            "function_name": call["function_name"],
                            "arguments": call["arguments"],
                            "extra": {"timing": call.get("timing")} if call.get("timing") else None,
                        }
                    )
                    for call in step["tool_calls"]
                ]
            if step.get("observations"):
                atif_step["observation"] = {
                    "results": [
                        clean(
                            {
                                "source_call_id": observation.get("source_call_id"),
                                "content": observation.get("content", ""),
                                "extra": {
                                    "status": observation.get("status"),
                                    **as_dict(observation.get("extra")),
                                },
                            }
                        )
                        for observation in step["observations"]
                    ]
                }
            metrics = as_dict(step.get("metrics"))
            if metrics:
                atif_step["metrics"] = clean(metrics)
                prompt = number(metrics.get("prompt_tokens"))
                completion = number(metrics.get("completion_tokens"))
                cached = number(metrics.get("cached_tokens"))
                cost = number(metrics.get("cost_usd"))
                if prompt is not None:
                    prompt_total += prompt
                    seen_prompt = True
                if completion is not None:
                    completion_total += completion
                    seen_completion = True
                if cached is not None:
                    cached_total += cached
                    seen_cached = True
                if cost is not None:
                    cost_total += cost
                    seen_cost = True
        atif_step["extra"] = clean(
            {
                "capture": step.get("extra"),
                "provider_name": step.get("provider_name"),
            }
        )
        atif_steps.append(clean(atif_step))

    final_metrics: dict[str, Any] = {"total_steps": len(atif_steps)}
    if seen_prompt:
        final_metrics["total_prompt_tokens"] = prompt_total
    if seen_completion:
        final_metrics["total_completion_tokens"] = completion_total
    if seen_cached:
        final_metrics["total_cached_tokens"] = cached_total
    if seen_cost:
        final_metrics["total_cost_usd"] = cost_total
    final_metrics["extra"] = {"timing_fidelity": run["fidelity"]["timing"]}
    trajectory_id = "traj-" + hashlib.sha256(run["session_id"].encode("utf-8")).hexdigest()[:20]
    agent = clean(
        {
            "name": run["agent"]["name"],
            "version": run["agent"]["version"],
            "model_name": run["agent"].get("model_name"),
            "extra": {
                "provider_name": run["agent"].get("provider_name"),
                "capture_source": run["source"]["format"],
                "adapter_version": ADAPTER_VERSION,
            },
        }
    )
    atif = {
        "schema_version": ATIF_SCHEMA,
        "session_id": run["session_id"],
        "trajectory_id": trajectory_id,
        "agent": agent,
        "steps": atif_steps,
        "notes": "Converted from a preserved CLI export; missing source fields were not inferred.",
        "final_metrics": final_metrics,
        "extra": {
            "run_ir_schema": RUN_IR_SCHEMA,
            "otel_schema_url": OTEL_SCHEMA_URL,
            "capture_fidelity": run["fidelity"],
        },
    }
    validate_atif(atif)
    return atif


def validate_atif(atif: dict[str, Any]) -> None:
    if atif.get("schema_version") != ATIF_SCHEMA:
        raise TrajectoryError("internal ATIF schema version mismatch")
    if not isinstance(atif.get("agent"), dict) or not atif["agent"].get("name") or not atif["agent"].get("version"):
        raise TrajectoryError("ATIF agent name and version are required")
    steps = atif.get("steps")
    if not isinstance(steps, list) or not steps:
        raise TrajectoryError("ATIF requires at least one step")
    for expected, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or step.get("step_id") != expected:
            raise TrajectoryError("ATIF step_id values must be sequential")
        if step.get("source") not in ("system", "user", "agent"):
            raise TrajectoryError("ATIF step source is invalid")
        if not isinstance(step.get("message"), (str, list)):
            raise TrajectoryError("ATIF step message is invalid")
        calls = as_list(step.get("tool_calls"))
        ids = {call.get("tool_call_id") for call in calls if isinstance(call, dict)}
        for result in as_list(as_dict(step.get("observation")).get("results")):
            source_call_id = as_dict(result).get("source_call_id")
            if source_call_id is not None and source_call_id not in ids:
                raise TrajectoryError("ATIF observation references an unknown tool_call_id")


def otel_attribute(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        encoded = {"boolValue": value}
    elif isinstance(value, int) and not isinstance(value, bool):
        encoded = {"intValue": str(value)}
    elif isinstance(value, float):
        encoded = {"doubleValue": value}
    else:
        encoded = {"stringValue": str(value)}
    return {"key": key, "value": encoded}


def stable_id(seed: str, length: int) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:length]


def assigned_times(run: dict[str, Any]) -> list[tuple[int, int, bool]]:
    observed_values = []
    for step in run["steps"]:
        value = as_dict(step.get("timing"))
        if value.get("observed") is True:
            observed_values.append(int(value["start_unix_nano"]))
    anchor = min(observed_values) if observed_values else to_unix_nano(run["created_at"])
    assert anchor is not None
    result = []
    for index, step in enumerate(run["steps"], start=1):
        value = as_dict(step.get("timing"))
        if value.get("observed") is True:
            start = int(value["start_unix_nano"])
            end = int(value["end_unix_nano"])
            result.append((start, max(start, end), False))
        else:
            start = anchor + index * 1_000
            result.append((start, start, True))
    return result


def build_otlp(run: dict[str, Any]) -> dict[str, Any]:
    trace_id = stable_id(run["session_id"], 32)
    root_span_id = stable_id(f"{run['session_id']}:root", 16)
    times = assigned_times(run)
    root_start = min(start for start, _, _ in times)
    root_end = max(end for _, end, _ in times)
    input_messages = [
        {"role": step["source"], "content": step.get("message", "")}
        for step in run["steps"]
        if step["source"] in ("system", "user")
    ]
    output_messages = [
        {
            "role": "assistant",
            "content": step.get("message", ""),
            "tool_calls": step.get("tool_calls", []),
        }
        for step in run["steps"]
        if step["source"] == "agent"
    ]
    root_attributes = [
        otel_attribute("gen_ai.operation.name", "invoke_agent"),
        otel_attribute("gen_ai.agent.name", run["agent"]["name"]),
        otel_attribute("gen_ai.agent.version", run["agent"]["version"]),
        otel_attribute("gen_ai.conversation.id", run["session_id"]),
        otel_attribute("gen_ai.input.messages", compact_json(input_messages)),
        otel_attribute("gen_ai.output.messages", compact_json(output_messages)),
        otel_attribute("agent.eval.capture.fidelity", compact_json(run["fidelity"])),
    ]
    if run["agent"].get("model_name"):
        root_attributes.append(otel_attribute("gen_ai.request.model", run["agent"]["model_name"]))
    if run["agent"].get("provider_name"):
        root_attributes.append(otel_attribute("gen_ai.provider.name", run["agent"]["provider_name"]))
    spans: list[dict[str, Any]] = [
        {
            "traceId": trace_id,
            "spanId": root_span_id,
            "name": f"invoke_agent {run['agent']['name']}",
            "kind": 1,
            "startTimeUnixNano": str(root_start),
            "endTimeUnixNano": str(root_end),
            "attributes": root_attributes,
            "status": {"code": 1},
        }
    ]
    for index, step in enumerate(run["steps"], start=1):
        if step["source"] != "agent":
            continue
        start, end, synthetic = times[index - 1]
        span_id = stable_id(f"{run['session_id']}:step:{index}", 16)
        metrics = as_dict(step.get("metrics"))
        model = step.get("model_name") or run["agent"].get("model_name") or "unknown"
        attributes = [
            otel_attribute("agent.eval.step.id", index),
            otel_attribute("agent.eval.time.synthetic", synthetic),
            otel_attribute(
                "gen_ai.output.messages",
                compact_json([{"role": "assistant", "content": step.get("message", "")}]),
            ),
        ]
        if metrics:
            attributes.append(otel_attribute("gen_ai.operation.name", "chat"))
            attributes.append(otel_attribute("gen_ai.request.model", model))
            mapping = {
                "prompt_tokens": "gen_ai.usage.input_tokens",
                "completion_tokens": "gen_ai.usage.output_tokens",
                "cached_tokens": "gen_ai.usage.cache_read.input_tokens",
            }
            for source_key, otel_key in mapping.items():
                value = number(metrics.get(source_key))
                if value is not None:
                    attributes.append(otel_attribute(otel_key, value))
            reasoning = number(as_dict(metrics.get("extra")).get("reasoning_tokens"))
            if reasoning is not None:
                attributes.append(otel_attribute("gen_ai.usage.reasoning.output_tokens", reasoning))
            cache_write = number(as_dict(metrics.get("extra")).get("cache_write_tokens"))
            if cache_write is not None:
                attributes.append(otel_attribute("gen_ai.usage.cache_creation.input_tokens", cache_write))
        spans.append(
            {
                "traceId": trace_id,
                "spanId": span_id,
                "parentSpanId": root_span_id,
                "name": f"chat {model}" if metrics else "agent_step",
                "kind": 3 if metrics else 1,
                "startTimeUnixNano": str(start),
                "endTimeUnixNano": str(end),
                "attributes": attributes,
                "status": {"code": 2 if as_dict(step.get("extra")).get("error") else 1},
            }
        )
        observations = {item.get("source_call_id"): item for item in as_list(step.get("observations"))}
        for tool_index, call in enumerate(as_list(step.get("tool_calls")), start=1):
            call_value = as_dict(call)
            call_id = str(call_value.get("tool_call_id") or f"step-{index}-tool-{tool_index}")
            observation = as_dict(observations.get(call_id))
            call_timing = as_dict(call_value.get("timing"))
            tool_start = int(call_timing.get("start_unix_nano", start))
            tool_end = int(call_timing.get("end_unix_nano", end))
            tool_status = str(observation.get("status") or "unknown")
            tool_attributes = [
                otel_attribute("gen_ai.operation.name", "execute_tool"),
                otel_attribute("gen_ai.tool.name", call_value.get("function_name", "tool")),
                otel_attribute("gen_ai.tool.call.id", call_id),
                otel_attribute("gen_ai.tool.call.arguments", compact_json(call_value.get("arguments", {}))),
                otel_attribute("gen_ai.tool.call.result", observation.get("content", "")),
                otel_attribute("agent.eval.tool.status", tool_status),
            ]
            spans.append(
                {
                    "traceId": trace_id,
                    "spanId": stable_id(f"{run['session_id']}:step:{index}:tool:{call_id}", 16),
                    "parentSpanId": span_id,
                    "name": f"execute_tool {call_value.get('function_name', 'tool')}",
                    "kind": 1,
                    "startTimeUnixNano": str(tool_start),
                    "endTimeUnixNano": str(max(tool_start, tool_end)),
                    "attributes": tool_attributes,
                    "status": {"code": 2 if tool_status in ("error", "failed") else 1},
                }
            )
    resource_attributes = [
        otel_attribute("service.name", "agent-eval-recorder"),
        otel_attribute("service.version", ADAPTER_VERSION),
        otel_attribute("agent.eval.capture.source", run["source"]["format"]),
        otel_attribute("agent.eval.input.sanitized", run["source"]["input_sanitized"]),
    ]
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": resource_attributes},
                "scopeSpans": [
                    {
                        "scope": {"name": "agent-eval.trajectory-converter", "version": ADAPTER_VERSION},
                        "schemaUrl": OTEL_SCHEMA_URL,
                        "spans": spans,
                    }
                ],
            }
        ]
    }


def build_trajectory_metrics(run: dict[str, Any], atif: dict[str, Any]) -> dict[str, Any]:
    """Compute source-independent counters without judging whether the result is correct."""
    user_steps = sum(1 for step in run["steps"] if step["source"] == "user")
    agent_steps = sum(1 for step in run["steps"] if step["source"] == "agent")
    tool_calls = 0
    tool_errors = 0
    duplicate_tool_calls = 0
    seen_tool_signatures: set[str] = set()
    for step in run["steps"]:
        observations = {
            item.get("source_call_id"): item
            for item in as_list(step.get("observations"))
            if isinstance(item, dict)
        }
        for call in as_list(step.get("tool_calls")):
            call_value = as_dict(call)
            tool_calls += 1
            signature = stable_id(
                compact_json(
                    {
                        "function_name": call_value.get("function_name", "tool"),
                        "arguments": call_value.get("arguments", {}),
                    }
                ),
                32,
            )
            if signature in seen_tool_signatures:
                duplicate_tool_calls += 1
            else:
                seen_tool_signatures.add(signature)
            observation = as_dict(observations.get(call_value.get("tool_call_id")))
            if str(observation.get("status") or "unknown").lower() in ("error", "failed"):
                tool_errors += 1

    final_metrics = as_dict(atif.get("final_metrics"))
    prompt_tokens = number(final_metrics.get("total_prompt_tokens"))
    completion_tokens = number(final_metrics.get("total_completion_tokens"))
    cached_tokens = number(final_metrics.get("total_cached_tokens"))
    total_tokens = None
    if prompt_tokens is not None or completion_tokens is not None:
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

    duration_ms = None
    if run["fidelity"].get("timing") == "observed":
        starts = [int(step["timing"]["start_unix_nano"]) for step in run["steps"]]
        ends = [int(step["timing"]["end_unix_nano"]) for step in run["steps"]]
        duration_ms = (max(ends) - min(starts)) / 1_000_000

    return clean(
        {
            "schema_version": "agent-eval-trajectory-metrics/v1",
            "session_id": run["session_id"],
            "total_steps": len(run["steps"]),
            "user_steps": user_steps,
            "agent_steps": agent_steps,
            "human_followup_steps": max(user_steps - 1, 0),
            "tool_calls": tool_calls,
            "tool_errors": tool_errors,
            "duplicate_tool_calls": duplicate_tool_calls,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
            "total_tokens": total_tokens,
            "cost_usd": number(final_metrics.get("total_cost_usd")),
            "duration_ms": duration_ms,
            "timing_fidelity": run["fidelity"].get("timing"),
            "unmapped_source_item_types": run["fidelity"].get("unmapped_source_item_types", []),
        }
    )


def trajectory(args: argparse.Namespace) -> int:
    input_path = Path(args.input).expanduser().resolve()
    source_text = read_text(input_path, args.max_chars)
    task_text = read_text(Path(args.task).expanduser().resolve(), args.max_chars).strip() if args.task else None
    agent_name = args.agent_name or ("codex" if args.source == "codex-jsonl" else "opencode")
    if not args.agent_version.strip():
        raise TrajectoryError("--agent-version must not be empty")
    if args.source == "codex-jsonl":
        if not task_text:
            raise TrajectoryError("Codex JSONL does not contain the initial prompt; --task is required")
        if not args.model or not args.model.strip():
            raise TrajectoryError("Codex JSONL does not identify the model; --model is required")
        parsed = parse_codex(
            source_text,
            task_text,
            args.session_id,
            agent_name,
            args.agent_version,
            args.model,
            args.provider,
        )
        raw_name = "codex-events.jsonl"
    else:
        parsed = parse_opencode(
            source_text,
            task_text,
            args.session_id,
            agent_name,
            args.agent_version,
            args.model,
            args.provider,
        )
        raw_name = "opencode-session.json"

    created_at = utc_now()
    fidelity = parsed["fidelity"]
    fidelity["timing"] = timing_fidelity(parsed["steps"])
    run = clean(
        {
            "schema_version": RUN_IR_SCHEMA,
            "created_at": created_at,
            "source": {
                "format": args.source,
                "input_sanitized": args.input_sanitized,
                "raw_file": f"raw/{raw_name}",
                "adapter_version": ADAPTER_VERSION,
            },
            **parsed,
        }
    )
    atif = build_atif(run)
    otlp = build_otlp(run)
    metrics = build_trajectory_metrics(run, atif)
    output_dir = Path(args.output_dir).expanduser().resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not args.force:
        raise TrajectoryError(f"output directory is not empty: {output_dir}; pass --force to replace known files")
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output_dir, 0o700)
    raw_path = output_dir / "raw" / raw_name
    atomic_write_text(raw_path, source_text, args.force)
    atomic_write_json(output_dir / "run-ir.json", run, args.force)
    atomic_write_json(output_dir / "trace.otlp.json", otlp, args.force)
    atomic_write_json(output_dir / "trajectory.atif.json", atif, args.force)
    atomic_write_json(output_dir / "trajectory-metrics.json", metrics, args.force)
    manifest = {
        "schema_version": "agent-eval-bundle/v1",
        "created_at": created_at,
        "session_id": run["session_id"],
        "source_format": args.source,
        "input_sanitized": args.input_sanitized,
        "run_ir_schema": RUN_IR_SCHEMA,
        "otel_schema_url": OTEL_SCHEMA_URL,
        "atif_schema_version": ATIF_SCHEMA,
        "adapter_version": ADAPTER_VERSION,
        "timing_fidelity": fidelity["timing"],
        "files": {
            "raw": f"raw/{raw_name}",
            "run_ir": "run-ir.json",
            "otlp": "trace.otlp.json",
            "atif": "trajectory.atif.json",
            "metrics": "trajectory-metrics.json",
        },
    }
    atomic_write_json(output_dir / "manifest.json", manifest, args.force)
    print(f"Trajectory bundle: {output_dir}")
    print(f"Source: {args.source}")
    print(f"Session: {run['session_id']}")
    print(f"Steps: {len(run['steps'])}")
    print(f"Timing fidelity: {fidelity['timing']}")
    if not args.input_sanitized:
        print("Warning: bundle contains unsanitized transcript/tool content; keep it local and do not commit it")
    return 0


def configure_trajectory_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("trajectory", help="convert a Codex/OpenCode export to OTLP JSON and ATIF")
    parser.add_argument("--source", required=True, choices=("codex-jsonl", "opencode-export"))
    parser.add_argument("--input", required=True, help="Codex JSONL or OpenCode export JSON")
    parser.add_argument("--task", help="canonical task text; required for Codex JSONL")
    parser.add_argument("--session-id", help="override a missing or unstable source session id")
    parser.add_argument("--agent-name", help="agent/harness name; defaults to codex or opencode")
    parser.add_argument("--agent-version", required=True)
    parser.add_argument("--model", help="model override; required for Codex JSONL")
    parser.add_argument("--provider", help="provider override when the source does not expose it")
    parser.add_argument("--input-sanitized", action="store_true", help="record that the input was already sanitized")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-chars", type=positive_int, default=10_000_000)
    parser.add_argument("--force", action="store_true")
    parser.set_defaults(handler=trajectory)
