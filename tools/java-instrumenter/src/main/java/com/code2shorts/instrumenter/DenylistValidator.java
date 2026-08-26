package com.code2shorts.instrumenter;

import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.expr.LambdaExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.MethodReferenceExpr;
import com.github.javaparser.ast.expr.ObjectCreationExpr;
import com.github.javaparser.ast.type.ClassOrInterfaceType;
import java.util.List;
import java.util.Set;

/**
 * Static AST-level pre-check, run before any instrumentation is attempted.
 * Restricts nondeterminism rather than trying to normalize it after
 * execution (ADR-005): threads, java.util.concurrent, Random, wall-clock
 * and environment access are all rejected outright. Also rejects
 * constructs that are simply out of Phase 2's supported scope (lambdas,
 * method references, the Stream API).
 */
public final class DenylistValidator {

    private DenylistValidator() {}

    private static final Set<String> DENIED_TYPE_NAMES = Set.of("Thread", "Random");

    private static final Set<String> DENIED_STATIC_CALLS =
            Set.of(
                    "Math.random",
                    "System.currentTimeMillis",
                    "System.nanoTime",
                    "System.getenv",
                    "System.getProperty");

    public static void validate(CompilationUnit unit) throws UnsupportedConstructException {
        checkImports(unit);
        checkTypeUsage(unit);
        checkStaticCalls(unit);
        checkLambdasAndStreams(unit);
    }

    private static void checkImports(CompilationUnit unit) throws UnsupportedConstructException {
        for (ImportDeclaration imp : unit.getImports()) {
            String name = imp.getNameAsString();
            if (name.startsWith("java.util.concurrent") || name.startsWith("java.lang.Thread")) {
                throw new UnsupportedConstructException(
                        "denied import (concurrency is not supported): " + name);
            }
        }
    }

    private static void checkTypeUsage(CompilationUnit unit) throws UnsupportedConstructException {
        List<ClassOrInterfaceType> types = unit.findAll(ClassOrInterfaceType.class);
        for (ClassOrInterfaceType type : types) {
            if (DENIED_TYPE_NAMES.contains(type.getNameAsString())) {
                throw new UnsupportedConstructException(
                        "denied type (nondeterministic or concurrent): " + type.getNameAsString());
            }
        }
        for (ObjectCreationExpr creation : unit.findAll(ObjectCreationExpr.class)) {
            String typeName = creation.getType().getNameAsString();
            if (DENIED_TYPE_NAMES.contains(typeName)) {
                throw new UnsupportedConstructException("denied construction: new " + typeName);
            }
        }
    }

    private static void checkStaticCalls(CompilationUnit unit) throws UnsupportedConstructException {
        for (MethodCallExpr call : unit.findAll(MethodCallExpr.class)) {
            if (call.getScope().isEmpty()) {
                continue;
            }
            String qualified = call.getScope().get().toString() + "." + call.getNameAsString();
            if (DENIED_STATIC_CALLS.contains(qualified)) {
                throw new UnsupportedConstructException(
                        "denied API call (nondeterministic): " + qualified + "(...)");
            }
        }
    }

    private static void checkLambdasAndStreams(CompilationUnit unit)
            throws UnsupportedConstructException {
        if (!unit.findAll(LambdaExpr.class).isEmpty()) {
            throw new UnsupportedConstructException("lambdas are not supported in Phase 2");
        }
        if (!unit.findAll(MethodReferenceExpr.class).isEmpty()) {
            throw new UnsupportedConstructException("method references are not supported in Phase 2");
        }
        for (MethodCallExpr call : unit.findAll(MethodCallExpr.class)) {
            if ("stream".equals(call.getNameAsString())) {
                throw new UnsupportedConstructException("the Stream API is not supported in Phase 2");
            }
        }
    }
}
