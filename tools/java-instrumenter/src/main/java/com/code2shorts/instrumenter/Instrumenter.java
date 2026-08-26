package com.code2shorts.instrumenter;

import com.github.javaparser.ParseProblemException;
import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * CLI entry point, invoked as `java -jar instrumenter.jar <input.java>`
 * by the Python-side JavaSourceInstrumenter via execution.sandbox.run_subprocess.
 *
 * Contract: exit 0 -> instrumented source printed to stdout, nothing else.
 * exit != 0 -> a single "REASON_CODE: message" line on stderr, stdout
 * empty. Never partial output on either stream.
 */
public final class Instrumenter {

    private Instrumenter() {}

    public static void main(String[] args) {
        if (args.length != 1) {
            System.err.println("USAGE_ERROR: expected exactly one argument: <input.java>");
            System.exit(2);
        }
        try {
            String source = Files.readString(Path.of(args[0]), StandardCharsets.UTF_8);
            CompilationUnit unit = StaticJavaParser.parse(source);
            DenylistValidator.validate(unit);
            if (unit.getTypes().isEmpty()) {
                throw new UnsupportedConstructException("no top-level type declaration found");
            }
            String className = unit.getTypes().get(0).getNameAsString();
            new SourceInstrumenter(className).instrument(unit);
            System.out.print(unit.toString());
            System.exit(0);
        } catch (ParseProblemException e) {
            System.err.println("PARSE_ERROR: " + e.getMessage());
            System.exit(1);
        } catch (UnsupportedConstructException e) {
            System.err.println("UNSUPPORTED_CONSTRUCT: " + e.getMessage());
            System.exit(1);
        } catch (IOException e) {
            System.err.println("IO_ERROR: " + e.getMessage());
            System.exit(1);
        } catch (RuntimeException e) {
            System.err.println("INSTRUMENTATION_ERROR: " + e.getClass().getSimpleName() + ": " + e.getMessage());
            System.exit(1);
        }
    }
}
