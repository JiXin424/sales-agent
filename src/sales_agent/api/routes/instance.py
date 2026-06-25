"""Instance-level runtime config endpoint."""

import re
from fastapi import APIRouter
from sales_agent.core.tenant_runtime import get_tenant_runtime

router = APIRouter(prefix="/instance", tags=["instance"])

# Fields whose VALUES should be treated as sensitive (masked).
# These patterns match against the field NAME.
_SENSITIVE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r".*_API_KEY$",
        r".*_SECRET$",
        r".*_TOKEN$",
        r".*_AES_KEY$",
        r".*_ENCRYPT_TOKEN$",
        r".*_PASSWORD$",
        r".*_PWD$",
        r".*_PASS$",
    ]
]

# Group assignment: (field_name_regex, group_name, sort_order)
_GROUP_RULES = [
    (r"^DEPLOYMENT_", "deployment", 0),
    (r"^TENANT_", "deployment", 0),
    (r"^MODEL_", "model", 1),
    (r"^EMBEDDING_", "model", 1),
    (r"^VECTOR_", "storage", 2),
    (r"^DATA_DIR$", "storage", 2),
    (r"^LOG_DIR$", "storage", 2),
    (r"^DINGTALK_MEDIA_", "media", 4),
    (r"^DINGTALK_VISION_", "media", 4),
    (r"^DINGTALK_AUDIO_", "media", 4),
    (r"^DINGTALK_QUICK_ENTRY", "coach", 5),
    (r"^DINGTALK_", "dingtalk", 3),
    (r"^COACH_", "coach", 5),
    (r"^NEO4J_", "neo4j", 6),
    (r"^ONTOLOGY_", "ontology", 7),
]


def _is_sensitive(field_name: str) -> bool:
    for pat in _SENSITIVE_PATTERNS:
        if pat.match(field_name):
            return True
    return False


def _mask_value(value: str) -> str:
    """Show first 6 chars + '...' + last 4 chars. If value too short, just mask fully."""
    if len(value) <= 10:
        return value[:3] + "..." if len(value) > 3 else "..."
    return value[:6] + "..." + value[-4:]


def _assign_group(field_name: str) -> str:
    for pattern, group, _ in _GROUP_RULES:
        if re.match(pattern, field_name):
            return group
    return "other"


@router.get("/config")
async def get_instance_config():
    """返回当前实例的完整运行时配置。

    API key / secret / token 等敏感字段不会返回明文，而是标记 sensitive=true
    并提供截断值。前端须经过用户交互（点击）才能看到完整值。
    """
    runtime = get_tenant_runtime()
    raw = runtime.get_all_env_vars()

    # Grouped output: preserve sort order within each group
    groups: dict[str, dict[str, object]] = {}
    group_order: dict[str, int] = {}

    for key, raw_value in sorted(raw.items()):
        group_name = _assign_group(key)
        if group_name not in groups:
            groups[group_name] = {}
            # Determine sort order for this group
            for pattern, g, order in _GROUP_RULES:
                if g == group_name:
                    group_order[group_name] = order
                    break
            else:
                group_order[group_name] = 99

        sensitive = _is_sensitive(key)
        if sensitive:
            groups[group_name][key] = {
                "value": raw_value,          # full plaintext (frontend gates via click)
                "sensitive": True,
                "masked": _mask_value(raw_value),  # truncated preview after reveal
            }
        else:
            groups[group_name][key] = raw_value

    # Sort groups by their defined order
    sorted_groups = dict(
        sorted(groups.items(), key=lambda item: group_order.get(item[0], 99))
    )

    return {"groups": sorted_groups}
