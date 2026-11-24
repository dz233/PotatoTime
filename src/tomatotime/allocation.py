"""任务时间归属。原始计时段只计一次，划分按有效计时片相交计算。"""

from datetime import timedelta
import json

from .core import parse, stamp


class OverlapError(ValueError):
    def __init__(self, message, record_id, end):
        super().__init__(message)
        self.record_id, self.conflict_end = record_id, end


def references(record):
    tasks = json.loads(record.get("tasks") or "[]")
    if not tasks and record.get("task_id"):
        tasks = [{"id": record["task_id"], "name": record.get("task_name") or "历史任务"}]
    return tasks


def task_title(record):
    return "、".join(task["name"] for task in references(record)) or "未关联任务"


def effective_before(intervals, boundary):
    seconds = 0.0
    for fragment in intervals:
        start, end = parse(fragment["start"]), parse(fragment["end"])
        length = (end - start).total_seconds()
        if length > 0 and boundary > start:
            seconds += float(fragment["seconds"]) * max(0, min(length, (boundary - start).total_seconds())) / length
    return seconds


def slice_elapsed(intervals, first, last):
    result, cursor = [], 0.0
    for fragment in intervals:
        count = float(fragment["seconds"])
        if count <= 0:
            continue
        left, right = max(first, cursor), min(last, cursor + count)
        if right > left:
            start, end = parse(fragment["start"]), parse(fragment["end"])
            length = (end - start).total_seconds()
            result.append({"start": stamp(start + timedelta(seconds=(left - cursor) / count * length)),
                           "end": stamp(start + timedelta(seconds=(right - cursor) / count * length)), "seconds": right - left})
        cursor += count
    return result


def assignments(record):
    tasks = references(record)
    intervals = json.loads(record["intervals"])
    if record.get("allocation_mode", "auto") == "auto":
        return [{"task_id": tasks[0]["id"] if len(tasks) == 1 else None,
                 "task_name": tasks[0]["name"] if len(tasks) == 1 else "未细分任务" if tasks else "未关联任务",
                 "intervals": intervals, "seconds": record["seconds"]}]
    output = []
    for row in json.loads(record.get("allocations") or "[]"):
        end = record["seconds"] if row["to"] is None else min(row["to"], record["seconds"])
        if end <= row["from"] and row["to"] is not None:
            continue
        selected = slice_elapsed(intervals, row["from"], end)
        output.append({**row, "intervals": selected, "seconds": sum(f["seconds"] for f in selected)})
    return output
