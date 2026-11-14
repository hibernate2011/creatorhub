import json


def normalize_tags(value: list[str] | None) -> list[str] | None:
    """清理、去重标签；None 表示更新时不修改。"""
    if value is None:
        return None
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        tag = " ".join(str(raw or "").strip().split())[:24]
        key = tag.casefold()
        if not tag or key in seen:
            continue
        seen.add(key)
        result.append(tag)
        if len(result) >= 12:
            break
    return result


def load_tags(raw: str | None) -> list[str]:
    """兼容 JSON 与早期手工逗号分隔格式。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return normalize_tags([str(item) for item in data]) or []
    except (TypeError, ValueError):
        pass
    return normalize_tags(str(raw).replace("，", ",").split(",")) or []
