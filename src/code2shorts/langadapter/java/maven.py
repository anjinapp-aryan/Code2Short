"""Maven is the Java build boundary. We shell out to the real `mvn`
executable — no custom Java build system.
"""

from __future__ import annotations

import shutil

from code2shorts.execution.sandbox import ProcessResult, Workspace, run_subprocess

# Pinned so every generated workspace builds against the same known-good
# toolchain, resolved from the local ~/.m2 cache when present.
JUNIT_JUPITER_VERSION = "5.10.2"
MAVEN_COMPILER_PLUGIN_VERSION = "3.13.0"
MAVEN_SUREFIRE_PLUGIN_VERSION = "3.2.5"
JAVA_RELEASE = "17"


class MavenNotFoundError(RuntimeError):
    pass


def find_mvn_executable() -> str:
    executable = shutil.which("mvn") or shutil.which("mvn.cmd")
    if executable is None:
        raise MavenNotFoundError(
            "mvn executable not found on PATH. Install Maven and JDK 17."
        )
    return executable


def run_maven(goals: list[str], workspace: Workspace, timeout_seconds: float) -> ProcessResult:
    """Run `mvn <goals>` inside workspace.path, batch mode, no interactivity."""
    command = [find_mvn_executable(), "-B", "-Dstyle.color=never", *goals]
    return run_subprocess(command, cwd=workspace.path, timeout_seconds=timeout_seconds)


def render_pom_xml(group_id: str, artifact_id: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>{group_id}</groupId>
  <artifactId>{artifact_id}</artifactId>
  <version>1.0.0</version>
  <packaging>jar</packaging>
  <properties>
    <maven.compiler.release>{JAVA_RELEASE}</maven.compiler.release>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>{JUNIT_JUPITER_VERSION}</version>
      <scope>test</scope>
    </dependency>
  </dependencies>
  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-compiler-plugin</artifactId>
        <version>{MAVEN_COMPILER_PLUGIN_VERSION}</version>
      </plugin>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-surefire-plugin</artifactId>
        <version>{MAVEN_SUREFIRE_PLUGIN_VERSION}</version>
      </plugin>
    </plugins>
  </build>
</project>
"""
