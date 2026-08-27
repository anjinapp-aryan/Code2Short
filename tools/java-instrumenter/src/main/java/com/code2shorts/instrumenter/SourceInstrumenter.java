package com.code2shorts.instrumenter;

import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.NodeList;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.ArrayAccessExpr;
import com.github.javaparser.ast.expr.AssignExpr;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.UnaryExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.VariableDeclarationExpr;
import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.stmt.ExpressionStmt;
import com.github.javaparser.ast.stmt.ForStmt;
import com.github.javaparser.ast.stmt.IfStmt;
import com.github.javaparser.ast.stmt.ReturnStmt;
import com.github.javaparser.ast.stmt.Statement;
import com.github.javaparser.ast.stmt.WhileStmt;
import java.util.List;

/**
 * AST source-to-source instrumentation for the Phase 2 supported subset:
 * static/instance methods with a body, local variable declarations with an
 * initializer, simple assignment/increment/decrement of a plain variable,
 * if/else, while, for, and one-dimensional array reads/writes. Everything
 * else is left untouched (passed through unmodified) rather than guessed
 * at — DenylistValidator has already rejected constructs Phase 2 refuses
 * to trace at all; anything else simply isn't instrumented, which is safe
 * (it just means fewer events, never a wrong event).
 *
 * `main(String[] args)` gets a different transform (programStart/End +
 * exception capture) via {@link #instrumentMain(MethodDeclaration)} —
 * everything else goes through {@link #instrumentMethod(MethodDeclaration)}.
 */
public final class SourceInstrumenter {

    private static final String TRACE = "com.code2shorts.trace.Code2ShortsTrace";

    private final String className;
    private int tempCounter = 0;

    // Local variables declared WITHOUT an initializer (`String result;`)
    // are not definitely-assigned yet — reading their "old" value before
    // the first real assignment is not just semantically meaningless, it's
    // a javac compile error. Tracks such names for the CURRENT method only
    // (cleared at the start of instrumentMethod/instrumentMain); the first
    // assignment to a tracked name is treated as a declare (old_value =
    // null) and removes it from the set, same as a declaration with an
    // initializer.
    private final java.util.Set<String> uninitializedVars = new java.util.HashSet<>();

    // Phase 6: local variables whose DECLARED type is a supported
    // collection. Tracked per method, like uninitializedVars.
    //
    // A collection mutates through method CALLS (`seen.put(...)`,
    // `stack.push(...)`), which the assignment/array-subscript emit points
    // never see — so before Phase 6 a HashMap was observed exactly once,
    // as `{}`, for an entire run. Recording the declared type here lets a
    // mutating call re-observe the collection's semantic contents.
    private final java.util.Set<String> collectionVars = new java.util.HashSet<>();

    /** Declared types treated as observable collections. Interfaces are
     *  included because that is how they are normally declared
     *  (`Map<K,V> m = new HashMap<>()`). Concrete kind and ordering are
     *  decided at RUNTIME by Code2ShortsTrace.collection(), not here —
     *  the declared type cannot tell us whether the instance is ordered. */
    private static final java.util.Set<String> COLLECTION_TYPES =
            java.util.Set.of(
                    "Map", "HashMap", "LinkedHashMap", "TreeMap", "SortedMap",
                    "Deque", "ArrayDeque", "Queue", "Stack",
                    "List", "ArrayList", "LinkedList",
                    "Set", "HashSet", "LinkedHashSet", "TreeSet");

    /** Calls that can change a collection's contents. `peek`/`get`/`size`
     *  are deliberately absent: they observe without mutating, and emitting
     *  a state event for them would add frames that teach nothing new. */
    private static final java.util.Set<String> MUTATING_CALLS =
            java.util.Set.of(
                    "put", "putIfAbsent", "remove", "clear", "putAll",
                    "push", "pop", "add", "addFirst", "addLast",
                    "offer", "offerFirst", "offerLast",
                    "poll", "pollFirst", "pollLast",
                    "removeFirst", "removeLast", "addAll");

    public SourceInstrumenter(String className) {
        this.className = className;
    }

    public void instrument(com.github.javaparser.ast.CompilationUnit unit) {
        List<MethodDeclaration> methods = unit.findAll(MethodDeclaration.class);
        for (MethodDeclaration method : methods) {
            if (method.getBody().isEmpty()) {
                continue; // abstract/interface method — nothing to instrument
            }
            uninitializedVars.clear();
        collectionVars.clear();
            if (isMainMethod(method)) {
                instrumentMain(method);
            } else {
                instrumentMethod(method);
            }
        }
    }

    private boolean isMainMethod(MethodDeclaration method) {
        return method.getNameAsString().equals("main")
                && method.isStatic()
                && method.getParameters().size() == 1
                && method.getParameter(0).getType().asString().equals("String[]");
    }

    // ---- generic method instrumentation ------------------------------

    private void instrumentMethod(MethodDeclaration method) {
        BlockStmt body = method.getBody().get();
        instrumentBlock(body);

        String qualifiedName = className + "." + method.getNameAsString();
        int declLine = method.getBegin().get().line;
        boolean isVoid = method.getType().isVoidType();

        // Wrap every return statement in this method (findAll on the method
        // node only reaches this method's own body — Java methods can't nest).
        for (ReturnStmt returnStmt : method.findAll(ReturnStmt.class)) {
            int line = returnStmt.getBegin().map(p -> p.line).orElse(declLine);
            if (returnStmt.getExpression().isPresent()) {
                String exprText = returnStmt.getExpression().get().toString();
                int id = tempCounter++;
                Statement replacement =
                        StaticJavaParser.parseStatement(
                                String.format(
                                        "{ var __c2s_ret_%d = %s; %s.methodExit(\"%s\", %s.repr(__c2s_ret_%d), %d); return __c2s_ret_%d; }",
                                        id, exprText, TRACE, qualifiedName, TRACE, id, line, id));
                returnStmt.replace(replacement);
            } else {
                Statement replacement =
                        StaticJavaParser.parseStatement(
                                String.format(
                                        "{ %s.methodExit(\"%s\", null, %d); return; }",
                                        TRACE, qualifiedName, line));
                returnStmt.replace(replacement);
            }
        }

        if (isVoid && !endsWithReturnOrThrow(body)) {
            body.addStatement(
                    StaticJavaParser.parseStatement(
                            String.format(
                                    "%s.methodExit(\"%s\", null, %d);",
                                    TRACE, qualifiedName, declLine)));
        }

        body.addStatement(
                0,
                StaticJavaParser.parseStatement(
                        String.format("%s.methodEnter(\"%s\", %d);", TRACE, qualifiedName, declLine)));
    }

    private boolean endsWithReturnOrThrow(BlockStmt body) {
        if (body.getStatements().isEmpty()) {
            return false;
        }
        Statement last = body.getStatements().getLast().get();
        return last.isReturnStmt() || last.isThrowStmt();
    }

    // ---- main() special-case instrumentation --------------------------

    private void instrumentMain(MethodDeclaration method) {
        BlockStmt body = method.getBody().get();
        instrumentBlock(body);

        String template =
                "{\n"
                        + TRACE + ".programStart();\n"
                        + "boolean __c2s_failed = false;\n"
                        + "try {\n"
                        + "  __c2s_placeholder__();\n"
                        + "} catch (Throwable __c2s_t) {\n"
                        + "  " + TRACE + ".exceptionThrown(__c2s_t);\n"
                        + "  __c2s_failed = true;\n"
                        + "} finally {\n"
                        + "  " + TRACE + ".programEnd();\n"
                        + "}\n"
                        + "if (__c2s_failed) { System.exit(1); }\n"
                        + "}";
        BlockStmt wrapper = StaticJavaParser.parseBlock(template);
        BlockStmt tryBlock =
                wrapper.findFirst(com.github.javaparser.ast.stmt.TryStmt.class).get().getTryBlock();
        tryBlock.getStatements().clear();
        tryBlock.getStatements().addAll(body.getStatements());
        method.setBody(wrapper);
    }

    // ---- statement-list instrumentation (shared by both paths) --------

    private void instrumentBlock(BlockStmt block) {
        NodeList<Statement> result = new NodeList<>();
        for (Statement stmt : block.getStatements()) {
            instrumentStatement(stmt, result);
        }
        block.setStatements(result);
    }

    private void instrumentStatement(Statement stmt, NodeList<Statement> out) {
        if (stmt.isIfStmt()) {
            instrumentIf(stmt.asIfStmt());
            out.add(stmt);
        } else if (stmt.isWhileStmt()) {
            instrumentWhile(stmt.asWhileStmt());
            out.add(stmt);
        } else if (stmt.isForStmt()) {
            instrumentFor(stmt.asForStmt());
            out.add(stmt);
        } else if (stmt.isBlockStmt()) {
            instrumentBlock(stmt.asBlockStmt());
            out.add(stmt);
        } else if (stmt.isExpressionStmt()) {
            instrumentExpressionStmt(stmt.asExpressionStmt(), out);
        } else {
            out.add(stmt);
        }
    }

    private void instrumentIf(IfStmt ifStmt) {
        int line = ifStmt.getBegin().get().line;
        ifStmt.setCondition(wrapCondition(ifStmt.getCondition(), line));

        // then/else are mutually exclusive at runtime, so a variable a
        // then-branch definitely-assigns must NOT be treated as already
        // assigned while instrumenting the else branch (and vice versa) —
        // snapshot/restore uninitializedVars around each branch, then
        // conservatively merge: a name stays "uninitialized" after the
        // whole if/else unless EVERY branch assigned it.
        java.util.Set<String> before = new java.util.HashSet<>(uninitializedVars);

        normalizeToBlockAndRecurse(ifStmt.getThenStmt(), ifStmt::setThenStmt);
        java.util.Set<String> afterThen = new java.util.HashSet<>(uninitializedVars);

        java.util.Set<String> afterElse;
        if (ifStmt.getElseStmt().isPresent()) {
            // NOTE: only `uninitializedVars` is branch-scoped. `collectionVars`
            // must NOT be reset here — a collection declared before an
            // if/else is still the same variable inside and after it, and
            // clearing it here silently stopped every mutation inside a
            // branch from being observed.
            uninitializedVars.clear();
            uninitializedVars.addAll(before);
            Statement elseStmt = ifStmt.getElseStmt().get();
            if (elseStmt.isIfStmt()) {
                instrumentIf(elseStmt.asIfStmt());
            } else {
                normalizeToBlockAndRecurse(elseStmt, ifStmt::setElseStmt);
            }
            afterElse = new java.util.HashSet<>(uninitializedVars);
        } else {
            afterElse = before; // no else branch taken == nothing assigned on that path
        }

        uninitializedVars.clear();
        uninitializedVars.addAll(afterThen);
        uninitializedVars.addAll(afterElse);
    }

    private void instrumentWhile(WhileStmt whileStmt) {
        int line = whileStmt.getBegin().get().line;
        whileStmt.setCondition(wrapCondition(whileStmt.getCondition(), line));
        BlockStmt bodyBlock = asBlock(whileStmt.getBody());
        bodyBlock
                .getStatements()
                .add(0, StaticJavaParser.parseStatement(String.format("%s.loopIteration(%d);", TRACE, line)));
        whileStmt.setBody(bodyBlock);
        instrumentBlock(bodyBlock);
    }

    private void instrumentFor(ForStmt forStmt) {
        int line = forStmt.getBegin().get().line;
        forStmt.getCompare().ifPresent(cmp -> forStmt.setCompare(wrapCondition(cmp, line)));
        BlockStmt bodyBlock = asBlock(forStmt.getBody());

        // Report each loop counter's CURRENT value at the top of every
        // iteration. Without this, a counter declared in the for-init
        // (`for (int i = 0; ...)`) never appears in the trace at all: the
        // init is an Expression on the ForStmt, not an ExpressionStmt in a
        // block, so instrumentBlock never sees it. Loop counters are
        // exactly the variables that act as array pointers in the majority
        // of the supported algorithms, so an unreported counter means an
        // unrenderable pointer.
        //
        // Emitted after loopIteration() so the ordering reads
        // "iteration N, i = <value>". old_value is null because this is a
        // per-iteration observation, not an assignment we witnessed.
        java.util.List<String> counterNames = new java.util.ArrayList<>();
        for (Expression init : forStmt.getInitialization()) {
            if (init.isVariableDeclarationExpr()) {
                for (var declarator : init.asVariableDeclarationExpr().getVariables()) {
                    counterNames.add(declarator.getNameAsString());
                }
            }
        }
        for (int index = counterNames.size() - 1; index >= 0; index--) {
            String name = counterNames.get(index);
            bodyBlock
                    .getStatements()
                    .add(
                            0,
                            StaticJavaParser.parseStatement(
                                    String.format(
                                            "%s.assign(\"%s\", null, %s.repr(%s), %d);",
                                            TRACE, name, TRACE, name, line)));
        }

        bodyBlock
                .getStatements()
                .add(0, StaticJavaParser.parseStatement(String.format("%s.loopIteration(%d);", TRACE, line)));
        forStmt.setBody(bodyBlock);
        instrumentBlock(bodyBlock);
    }

    private Expression wrapCondition(Expression condition, int line) {
        String condText = condition.toString();
        return StaticJavaParser.parseExpression(
                String.format(
                        "%s.condition(%s, \"%s\", %d)", TRACE, condText, escape(condText), line));
    }

    private BlockStmt asBlock(Statement body) {
        if (body.isBlockStmt()) {
            return body.asBlockStmt();
        }
        BlockStmt block = new BlockStmt();
        block.addStatement(body);
        return block;
    }

    private void normalizeToBlockAndRecurse(Statement stmt, java.util.function.Consumer<Statement> setter) {
        BlockStmt block = asBlock(stmt);
        setter.accept(block);
        instrumentBlock(block);
    }

    private void instrumentExpressionStmt(ExpressionStmt stmt, NodeList<Statement> out) {
        Expression expr = stmt.getExpression();
        int line = stmt.getBegin().get().line;

        if (expr.isVariableDeclarationExpr()) {
            out.add(stmt);
            for (var declarator : expr.asVariableDeclarationExpr().getVariables()) {
                if (declarator.getInitializer().isEmpty()) {
                    uninitializedVars.add(declarator.getNameAsString());
                    continue;
                }
                Expression init = declarator.getInitializer().get();
                String name = declarator.getNameAsString();
                if (isCollectionType(declarator.getType())) {
                    // Register it, and record its initial (usually empty)
                    // state so the first frame shows a real observation
                    // rather than an assumed empty collection.
                    collectionVars.add(name);
                    out.add(buildCollectionObservation(name, "init", line));
                    continue;
                }
                // `int head = queue.poll();` — the declaration's VALUE
                // mutated a collection, so observe it after the statement.
                String consumed = mutatedCollectionName(init);
                if (consumed != null) {
                    out.add(buildCollectionObservation(consumed, mutatingCallName(init), line));
                }
                if (init.isArrayAccessExpr()) {
                    out.add(buildArrayRead(init.asArrayAccessExpr(), line));
                } else {
                    out.add(
                            StaticJavaParser.parseStatement(
                                    String.format(
                                            "%s.assign(\"%s\", null, %s.repr(%s), %d);",
                                            TRACE, name, TRACE, name, line)));
                }
            }
            return;
        }

        if (expr.isAssignExpr()) {
            AssignExpr assign = expr.asAssignExpr();
            Expression target = assign.getTarget();
            // `total = total + stack.pop();` — the assigned VALUE mutated a
            // collection. Recorded after the statement, like every other
            // collection observation.
            String consumedByValue = mutatedCollectionName(assign.getValue());
            if (consumedByValue != null) {
                Statement observation =
                        buildCollectionObservation(
                                consumedByValue, mutatingCallName(assign.getValue()), line);
                out.add(stmt);
                out.add(observation);
                return;
            }
            if (target.isArrayAccessExpr()) {
                ArrayAccessExpr arrayTarget = target.asArrayAccessExpr();
                if (assign.getValue().isArrayAccessExpr()) {
                    out.add(buildArrayRead(assign.getValue().asArrayAccessExpr(), line));
                }
                out.add(stmt);
                out.add(buildArrayWrite(arrayTarget, line));
                return;
            }
            if (target.isNameExpr()) {
                String name = target.asNameExpr().getNameAsString();
                if (uninitializedVars.remove(name)) {
                    // First real assignment to a `Type v;` declared without
                    // an initializer — reading v's "old" value here would
                    // be a definite-assignment compile error, so treat this
                    // exactly like a declare-with-initializer instead.
                    out.add(stmt);
                    out.add(
                            StaticJavaParser.parseStatement(
                                    String.format(
                                            "%s.assign(\"%s\", null, %s.repr(%s), %d);",
                                            TRACE, name, TRACE, name, line)));
                } else {
                    int id = tempCounter++;
                    out.add(
                            StaticJavaParser.parseStatement(
                                    String.format("var __c2s_old_%d = %s.repr(%s);", id, TRACE, name)));
                    out.add(stmt);
                    out.add(
                            StaticJavaParser.parseStatement(
                                    String.format(
                                            "%s.assign(\"%s\", __c2s_old_%d, %s.repr(%s), %d);",
                                            TRACE, name, id, TRACE, name, line)));
                }
                return;
            }
            out.add(stmt);
            return;
        }

        if (expr.isUnaryExpr()) {
            UnaryExpr unary = expr.asUnaryExpr();
            boolean isIncDec =
                    unary.getOperator() == UnaryExpr.Operator.PREFIX_INCREMENT
                            || unary.getOperator() == UnaryExpr.Operator.POSTFIX_INCREMENT
                            || unary.getOperator() == UnaryExpr.Operator.PREFIX_DECREMENT
                            || unary.getOperator() == UnaryExpr.Operator.POSTFIX_DECREMENT;
            if (isIncDec && unary.getExpression().isNameExpr()) {
                String name = unary.getExpression().asNameExpr().getNameAsString();
                int id = tempCounter++;
                out.add(
                        StaticJavaParser.parseStatement(
                                String.format("var __c2s_old_%d = %s.repr(%s);", id, TRACE, name)));
                out.add(stmt);
                out.add(
                        StaticJavaParser.parseStatement(
                                String.format(
                                        "%s.assign(\"%s\", __c2s_old_%d, %s.repr(%s), %d);",
                                        TRACE, name, id, TRACE, name, line)));
                return;
            }
        }

        // Phase 6: a mutating call on a tracked collection. The statement
        // runs first, then the collection is re-observed — so the emitted
        // state is what the collection actually holds AFTER the operation.
        String mutated = mutatedCollectionName(expr);
        if (mutated != null) {
            out.add(stmt);
            out.add(buildCollectionObservation(mutated, mutatingCallName(expr), line));
            return;
        }

        out.add(stmt);
    }

    /** The first mutating call on a tracked collection anywhere inside this
     *  expression, or null.
     *
     *  Searches the whole subtree rather than only the top-level expression,
     *  because a mutation is very often a VALUE: `int head = queue.poll();`
     *  and `total = total + stack.pop();` both change the collection while
     *  the statement itself is a declaration or an assignment. Matching only
     *  bare `queue.poll();` statements made a drained queue look as though
     *  it only ever filled — a visibly wrong video, not merely a thinner one.
     *
     *  Only a direct call on a simple name counts; a chained or computed
     *  receiver is left alone rather than guessed at. */
    private MethodCallExpr mutatingCall(Expression expr) {
        for (MethodCallExpr call : expr.findAll(MethodCallExpr.class)) {
            if (call.getScope().isEmpty() || !call.getScope().get().isNameExpr()) {
                continue;
            }
            String receiver = call.getScope().get().asNameExpr().getNameAsString();
            if (collectionVars.contains(receiver)
                    && MUTATING_CALLS.contains(call.getNameAsString())) {
                return call;
            }
        }
        return null;
    }

    private String mutatedCollectionName(Expression expr) {
        MethodCallExpr call = mutatingCall(expr);
        return call == null ? null : call.getScope().get().asNameExpr().getNameAsString();
    }

    private String mutatingCallName(Expression expr) {
        return mutatingCall(expr).getNameAsString();
    }

    private static boolean isCollectionType(com.github.javaparser.ast.type.Type type) {
        if (!type.isClassOrInterfaceType()) {
            return false;
        }
        return COLLECTION_TYPES.contains(type.asClassOrInterfaceType().getNameAsString());
    }

    /** Emits the collection's semantic contents. The runtime helper decides
     *  kind and ordering from the actual instance — the declared type
     *  cannot tell us whether a Map is insertion-ordered. */
    private Statement buildCollectionObservation(String name, String operation, int line) {
        return StaticJavaParser.parseStatement(
                String.format(
                        "%s.collection(\"%s\", %s, \"%s\", %d);",
                        TRACE, escape(name), name, escape(operation), line));
    }

    private Statement buildArrayRead(ArrayAccessExpr access, int line) {
        String arrayName = access.getName().toString();
        String indexText = access.getIndex().toString();
        return StaticJavaParser.parseStatement(
                String.format(
                        "%s.arrayRead(\"%s\", %s, %s.repr(%s), %d);",
                        TRACE, arrayName, indexText, TRACE, access.toString(), line));
    }

    private Statement buildArrayWrite(ArrayAccessExpr access, int line) {
        String arrayName = access.getName().toString();
        String indexText = access.getIndex().toString();
        return StaticJavaParser.parseStatement(
                String.format(
                        "%s.arrayWrite(\"%s\", %s, %s.repr(%s), %d);",
                        TRACE, arrayName, indexText, TRACE, access.toString(), line));
    }

    private static String escape(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
