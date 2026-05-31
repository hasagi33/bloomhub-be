"""`python manage.py ai_tools` — print the assistant tool registry grouped by module.

Use this to inspect coverage at a glance ("which module has how many tools?")
without poking the live registry from a shell. Supports --json for machine-
readable output and --module to focus on one module.
"""

from __future__ import annotations

import json as _json

from django.core.management.base import BaseCommand

from core.ai.tools.coverage import (
    format_coverage_table,
    gaps,
    module_counts,
    tool_coverage,
)


class Command(BaseCommand):
    help = "List the AI assistant tools registered per module."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit JSON instead of the formatted table.",
        )
        parser.add_argument(
            "--module",
            type=str,
            default=None,
            help="Only show tools from the given module.",
        )

    def handle(self, *args, **options):
        coverage = tool_coverage()
        if options["module"]:
            module = options["module"]
            coverage = {module: coverage.get(module, [])}

        if options["json"]:
            payload = {
                "coverage": coverage,
                "counts": module_counts(),
                "gaps": gaps(),
            }
            self.stdout.write(_json.dumps(payload, indent=2))
            return

        if options["module"]:
            tools = coverage.get(options["module"], [])
            self.stdout.write(self.style.SUCCESS(f"[{options['module']}]"))
            if not tools:
                self.stdout.write("  (no tools registered)")
                return
            for tool in tools:
                flags = []
                if tool["mutating"]:
                    flags.append("mutating")
                if tool["sensitive"]:
                    flags.append("sensitive")
                tag = f" ({', '.join(flags)})" if flags else ""
                self.stdout.write(f"  - {tool['name']}{tag}")
                self.stdout.write(f"      {tool['description']}")
            return

        self.stdout.write(format_coverage_table())
