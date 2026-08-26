"""JavaWorkspace: materializes GeneratedCode into an isolated Maven project.

Conceptually:

    temporary_workspace/
        pom.xml
        src/main/java/...
        src/test/java/...

GeneratedCode.source_files keys must already be Maven-convention relative
paths (e.g. "src/main/java/com/code2shorts/reversestring/Main.java").
"""

from __future__ import annotations

from code2shorts.codegen.base import GeneratedCode
from code2shorts.execution.sandbox import Workspace
from code2shorts.langadapter.java.maven import render_pom_xml

GROUP_ID = "com.code2shorts.generated"
ARTIFACT_ID = "code2shorts-java-workspace"

MAIN_SOURCE_ROOT = "src/main/java/"


class JavaWorkspace:
    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    @property
    def workspace(self) -> Workspace:
        return self._workspace

    def materialize(self, code: GeneratedCode) -> None:
        self._workspace.write_file("pom.xml", render_pom_xml(GROUP_ID, ARTIFACT_ID))
        for relative_path, content in code.source_files.items():
            self._workspace.write_file(relative_path, content)

    @staticmethod
    def main_class_name(entry_point: str) -> str:
        """Derive the fully-qualified class name from its source path.

        "src/main/java/com/code2shorts/reversestring/Main.java" ->
        "com.code2shorts.reversestring.Main"
        """
        normalized = entry_point.replace("\\", "/")
        if not normalized.startswith(MAIN_SOURCE_ROOT):
            raise ValueError(
                f"entry_point must live under {MAIN_SOURCE_ROOT!r}, got {entry_point!r}"
            )
        relative = normalized[len(MAIN_SOURCE_ROOT) :]
        if not relative.endswith(".java"):
            raise ValueError(f"entry_point must end with .java, got {entry_point!r}")
        return relative[: -len(".java")].replace("/", ".")
