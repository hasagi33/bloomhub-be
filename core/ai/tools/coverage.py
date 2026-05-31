"""Per-module coverage report for the assistant tool registry.

Until the monolithic `core/ai/tools/__init__.py` is split into one file per
module, this is the canonical place to ask "which module has how many
tools, what are they, and which are mutating/sensitive?".

Usage:
    from core.ai.tools.coverage import tool_coverage, format_coverage_table
    print(format_coverage_table())
"""

from __future__ import annotations

from typing import Any

from core.ai.tools import registry

# Suggested target module count — bump as new modules are exposed. Used by
# `gaps()` to flag modules that are documented in MODULE_PROMPTS but have
# zero registered tools.
KNOWN_MODULES: tuple[str, ...] = (
    "general",
    "employees",
    "vacations",
    "time_tracking",
    "assets",
    "documents",
    "onboarding",
    "notifications",
    "admin",
    "mobility_compensation",
    "reviews",
    "training",
)


def tool_coverage() -> dict[str, list[dict[str, Any]]]:
    """Return module → [tool descriptors] for every registered tool."""
    out: dict[str, list[dict[str, Any]]] = {}
    for module, tools in registry.by_module().items():
        out[module] = [
            {
                "name": t.name,
                "description": t.description,
                "mutating": t.mutating,
                "sensitive": t.sensitive,
                "requires_confirmation": t.requires_confirmation,
                "ui_path": t.ui_path,
                "required_permissions": list(t.required_permissions or ()),
                "workflow_topic": t.workflow_topic,
            }
            for t in tools
        ]
    return out


def module_counts() -> dict[str, dict[str, int]]:
    """Thin wrapper around the registry helper for callers that import only this module."""
    return registry.module_counts()


def gaps() -> list[str]:
    """List KNOWN_MODULES that currently have zero registered tools."""
    by_module = registry.by_module()
    return [m for m in KNOWN_MODULES if m not in by_module or not by_module[m]]


def format_coverage_table() -> str:
    """Plain-text table grouped by module with totals + tool names."""
    by_module = registry.by_module()
    counts = registry.module_counts()
    lines: list[str] = []
    lines.append(f"{'Module':25s} {'Total':>5} {'Read':>5} {'Mut':>4} {'Sens':>5}")
    lines.append("-" * 50)
    for module in sorted(by_module):
        c = counts[module]
        lines.append(
            f"{module:25s} {c['total']:>5} {c['read']:>5} "
            f"{c['mutating']:>4} {c['sensitive']:>5}"
        )
    grand_total = sum(c["total"] for c in counts.values())
    grand_mut = sum(c["mutating"] for c in counts.values())
    grand_sens = sum(c["sensitive"] for c in counts.values())
    grand_read = sum(c["read"] for c in counts.values())
    lines.append("-" * 50)
    lines.append(
        f"{'TOTAL':25s} {grand_total:>5} {grand_read:>5} "
        f"{grand_mut:>4} {grand_sens:>5}"
    )
    blank = gaps()
    if blank:
        lines.append("")
        lines.append(f"Modules with NO tools: {', '.join(blank)}")
    lines.append("")
    for module in sorted(by_module):
        lines.append(f"[{module}]")
        for tool in by_module[module]:
            flags = []
            if tool.mutating:
                flags.append("mutating")
            if tool.sensitive:
                flags.append("sensitive")
            tag = f" ({', '.join(flags)})" if flags else ""
            lines.append(f"  - {tool.name}{tag}")
        lines.append("")
    return "\n".join(lines)
