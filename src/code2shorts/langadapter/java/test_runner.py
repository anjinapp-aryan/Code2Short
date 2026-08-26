from __future__ import annotations

import re

from code2shorts.langadapter.base import TestResult
from code2shorts.langadapter.java.maven import run_maven
from code2shorts.langadapter.java.workspace import JavaWorkspace

_SUMMARY_LINE = re.compile(
    r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)"
)


class JavaTestRunner:
    def run(self, java_workspace: JavaWorkspace, timeout_seconds: float) -> TestResult:
        result = run_maven(["test"], java_workspace.workspace, timeout_seconds)
        tests_run, tests_passed = self._summarize_surefire_reports(java_workspace)
        succeeded = result.returncode == 0 and not result.timed_out
        return TestResult(
            succeeded=succeeded,
            stdout=result.stdout,
            stderr=result.stderr,
            tests_run=tests_run,
            tests_passed=tests_passed,
            duration_seconds=result.duration_seconds,
            timed_out=result.timed_out,
        )

    def _summarize_surefire_reports(
        self, java_workspace: JavaWorkspace
    ) -> tuple[int, int]:
        reports_dir = java_workspace.workspace.path / "target" / "surefire-reports"
        if not reports_dir.is_dir():
            return 0, 0

        total_run = total_failures = total_errors = total_skipped = 0
        for report_file in reports_dir.glob("*.txt"):
            match = _SUMMARY_LINE.search(report_file.read_text(encoding="utf-8"))
            if match is None:
                continue
            run, failures, errors, skipped = (int(group) for group in match.groups())
            total_run += run
            total_failures += failures
            total_errors += errors
            total_skipped += skipped

        total_passed = total_run - total_failures - total_errors - total_skipped
        return total_run, total_passed
