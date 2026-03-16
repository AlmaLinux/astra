import ast
from pathlib import Path

from django.test import SimpleTestCase


class TestLoggerExceptionIncludesExtra(SimpleTestCase):
    def test_all_logger_exception_calls_include_extra(self) -> None:
        """Enforce structured exception payloads.

        Policy: every `logger.exception(...)` call must include `extra=...` so
        log aggregation (Sentry Logs, JSON logs, etc.) can filter on normalized
        exception fields even when stack traces are truncated.
        """

        astra_app_root = Path(__file__).resolve().parents[2]
        missing: list[tuple[Path, int, str]] = []

        for path in astra_app_root.rglob("*.py"):
            # Migrations are auto-generated and not part of app logging policy.
            if "migrations" in path.parts:
                continue

            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue

                func = node.func
                if not (isinstance(func, ast.Attribute) and func.attr == "exception"):
                    continue

                base = func.value
                if not (isinstance(base, ast.Name) and base.id == "logger"):
                    continue

                if any(isinstance(k, ast.keyword) and k.arg == "extra" for k in node.keywords):
                    continue

                snippet = ast.get_source_segment(source, node) or ""
                missing.append((path.relative_to(astra_app_root), node.lineno, snippet.splitlines()[0].strip()))

        if missing:
            details = "\n".join(f"{p}:{lineno}: {snippet}" for p, lineno, snippet in sorted(missing))
            self.fail(f"Missing extra= in logger.exception calls:\n{details}\n")
