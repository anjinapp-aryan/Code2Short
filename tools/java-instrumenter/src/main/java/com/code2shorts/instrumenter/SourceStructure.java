package com.code2shorts.instrumenter;

import com.github.javaparser.ParseProblemException;
import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.TypeDeclaration;
import com.github.javaparser.ast.expr.ArrayAccessExpr;
import com.github.javaparser.ast.expr.AssignExpr;
import com.github.javaparser.ast.expr.BinaryExpr;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.UnaryExpr;
import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.stmt.ForStmt;
import com.github.javaparser.ast.stmt.IfStmt;
import com.github.javaparser.ast.stmt.Statement;
import com.github.javaparser.ast.stmt.WhileStmt;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/**
 * Phase 8.4 (experimental JDI backend): describes a source file's statement
 * structure as JSON, so line snapshots from a debugger-based tracer can be
 * turned back into the SAME events {@link SourceInstrumenter} emits.
 *
 * <p>Read-only: parses and prints, never rewrites. Invoked as
 * {@code java -cp instrumenter.jar com.code2shorts.instrumenter.SourceStructure <file>}
 * through execution.sandbox.run_subprocess. Same exit contract as
 * {@link Instrumenter}: exit 0 with JSON on stdout, or exit 1 with one
 * "REASON_CODE: message" line on stderr.
 *
 * <p>It reports exactly the facts the instrumenter's emit points depend on
 * (declarations, assignments, ++/--, if/while/for, returns, break/continue)
 * and marks everything else {@code "other"}. Expressions are only described
 * when they are side-effect free (names, literals, arithmetic, {@code .length},
 * array reads); anything else is {@code null}, so the caller can never
 * evaluate a call or an assignment by accident.
 */
public final class SourceStructure {

    private SourceStructure() {}

    public static void main(String[] args) {
        if (args.length != 1) {
            System.err.println("USAGE_ERROR: expected exactly one argument: <input.java>");
            System.exit(2);
        }
        try {
            String source = Files.readString(Path.of(args[0]), StandardCharsets.UTF_8);
            CompilationUnit unit = StaticJavaParser.parse(source);
            StringBuilder out = new StringBuilder();
            out.append("{\"methods\":[");
            boolean first = true;
            for (TypeDeclaration<?> type : unit.getTypes()) {
                for (MethodDeclaration method : type.findAll(MethodDeclaration.class)) {
                    if (method.getBody().isEmpty()) {
                        continue;
                    }
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    method(out, type.getNameAsString(), method);
                }
            }
            out.append("]}");
            System.out.print(out);
            System.exit(0);
        } catch (ParseProblemException e) {
            System.err.println("PARSE_ERROR: " + e.getMessage());
            System.exit(1);
        } catch (IOException e) {
            System.err.println("IO_ERROR: " + e.getMessage());
            System.exit(1);
        } catch (RuntimeException e) {
            System.err.println("STRUCTURE_ERROR: " + e.getClass().getSimpleName() + ": " + e.getMessage());
            System.exit(1);
        }
    }

    private static void method(StringBuilder out, String className, MethodDeclaration method) {
        boolean isMain = method.getNameAsString().equals("main")
                && method.isStatic()
                && method.getParameters().size() == 1
                && method.getParameter(0).getType().asString().equals("String[]");
        BlockStmt body = method.getBody().get();
        out.append("{\"class\":").append(str(className))
                .append(",\"name\":").append(str(method.getNameAsString()))
                .append(",\"declLine\":").append(method.getBegin().get().line)
                .append(",\"endLine\":").append(method.getEnd().get().line)
                .append(",\"isMain\":").append(isMain)
                .append(",\"isVoid\":").append(method.getType().isVoidType())
                .append(",\"body\":");
        statements(out, body.getStatements());
        out.append('}');
    }

    private static void statements(StringBuilder out, List<Statement> list) {
        out.append('[');
        for (int i = 0; i < list.size(); i++) {
            if (i > 0) {
                out.append(',');
            }
            statement(out, list.get(i));
        }
        out.append(']');
    }

    /** The closing-brace line of a `{...}` body, or null when the body is a
     *  single statement (no braces, so no scope of its own to close). */
    private static String blockEnd(Statement stmt) {
        return stmt.isBlockStmt() ? String.valueOf(stmt.getEnd().get().line) : "null";
    }

    private static List<Statement> block(Statement stmt) {
        return stmt.isBlockStmt() ? stmt.asBlockStmt().getStatements() : List.of(stmt);
    }

    private static void statement(StringBuilder out, Statement stmt) {
        out.append("{\"line\":").append(stmt.getBegin().get().line)
                .append(",\"endLine\":").append(stmt.getEnd().get().line)
                .append(",\"kind\":");
        if (stmt.isIfStmt()) {
            IfStmt s = stmt.asIfStmt();
            out.append("\"if\",\"cond\":").append(str(s.getCondition().toString()))
                    .append(",\"thenEnd\":").append(blockEnd(s.getThenStmt()))
                    .append(",\"elseEnd\":").append(s.getElseStmt().map(SourceStructure::blockEnd).orElse("null"))
                    .append(",\"then\":");
            statements(out, block(s.getThenStmt()));
            out.append(",\"else\":");
            if (s.getElseStmt().isPresent()) {
                statements(out, block(s.getElseStmt().get()));
            } else {
                out.append("null");
            }
        } else if (stmt.isWhileStmt()) {
            WhileStmt s = stmt.asWhileStmt();
            out.append("\"while\",\"cond\":").append(str(s.getCondition().toString()))
                    .append(",\"bodyEnd\":").append(blockEnd(s.getBody()))
                    .append(",\"body\":");
            statements(out, block(s.getBody()));
        } else if (stmt.isForStmt()) {
            ForStmt s = stmt.asForStmt();
            out.append("\"for\",\"cond\":")
                    .append(s.getCompare().map(c -> str(c.toString())).orElse("null"))
                    .append(",\"counters\":[");
            boolean first = true;
            for (Expression init : s.getInitialization()) {
                if (init.isVariableDeclarationExpr()) {
                    for (var d : init.asVariableDeclarationExpr().getVariables()) {
                        if (!first) {
                            out.append(',');
                        }
                        first = false;
                        out.append(str(d.getNameAsString()));
                    }
                }
            }
            out.append("],\"hasUpdate\":").append(!s.getUpdate().isEmpty())
                    .append(",\"bodyEnd\":").append(blockEnd(s.getBody()))
                    .append(",\"body\":");
            statements(out, block(s.getBody()));
        } else if (stmt.isBlockStmt()) {
            out.append("\"block\",\"bodyEnd\":").append(blockEnd(stmt))
                    .append(",\"body\":");
            statements(out, stmt.asBlockStmt().getStatements());
        } else if (stmt.isReturnStmt()) {
            out.append("\"return\",\"hasValue\":").append(stmt.asReturnStmt().getExpression().isPresent())
                    .append(",\"value\":")
                    .append(stmt.asReturnStmt().getExpression().map(SourceStructure::pure).orElse("null"))
                    .append(",\"valueIsCall\":")
                    .append(stmt.asReturnStmt().getExpression().map(e -> e.isMethodCallExpr()).orElse(false))
                    .append(",\"valueWithCall\":")
                    .append(stmt.asReturnStmt().getExpression().map(SourceStructure::withOneCall).orElse("null"));
        } else if (stmt.isBreakStmt()) {
            out.append("\"break\",\"labelled\":").append(stmt.asBreakStmt().getLabel().isPresent());
        } else if (stmt.isContinueStmt()) {
            out.append("\"continue\",\"labelled\":").append(stmt.asContinueStmt().getLabel().isPresent());
        } else if (stmt.isExpressionStmt()) {
            expressionStatement(out, stmt.asExpressionStmt().getExpression());
        } else {
            out.append("\"other\"");
        }
        out.append('}');
    }

    private static void expressionStatement(StringBuilder out, Expression expr) {
        if (expr.isVariableDeclarationExpr()) {
            out.append("\"decl\",\"vars\":[");
            boolean first = true;
            for (var d : expr.asVariableDeclarationExpr().getVariables()) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                out.append("{\"name\":").append(str(d.getNameAsString()))
                        .append(",\"type\":").append(str(d.getType().asString()))
                        .append(",\"hasInit\":").append(d.getInitializer().isPresent())
                        .append(",\"initRead\":")
                        .append(d.getInitializer().filter(Expression::isArrayAccessExpr)
                                .map(e -> read(e.asArrayAccessExpr())).orElse("null"))
                        .append(",\"initIsCall\":")
                        .append(d.getInitializer().map(Expression::isMethodCallExpr).orElse(false))
                        .append('}');
            }
            out.append(']');
            out.append(",\"hasCall\":").append(!expr.findAll(com.github.javaparser.ast.expr.MethodCallExpr.class).isEmpty());
            return;
        }
        if (expr.isAssignExpr()) {
            AssignExpr a = expr.asAssignExpr();
            out.append("\"assign\",\"operator\":").append(str(a.getOperator().asString()))
                    .append(",\"target\":");
            if (a.getTarget().isNameExpr()) {
                out.append("{\"kind\":\"name\",\"name\":").append(str(a.getTarget().asNameExpr().getNameAsString())).append('}');
            } else if (a.getTarget().isArrayAccessExpr()) {
                out.append("{\"kind\":\"array\",\"access\":").append(read(a.getTarget().asArrayAccessExpr())).append('}');
            } else {
                out.append("{\"kind\":\"other\"}");
            }
            out.append(",\"valueRead\":")
                    .append(a.getValue().isArrayAccessExpr() ? read(a.getValue().asArrayAccessExpr()) : "null")
                    .append(",\"valueIsCall\":").append(a.getValue().isMethodCallExpr())
                    .append(",\"hasCall\":").append(!a.findAll(com.github.javaparser.ast.expr.MethodCallExpr.class).isEmpty());
            return;
        }
        if (expr.isUnaryExpr()) {
            UnaryExpr u = expr.asUnaryExpr();
            boolean incDec = u.getOperator() == UnaryExpr.Operator.PREFIX_INCREMENT
                    || u.getOperator() == UnaryExpr.Operator.POSTFIX_INCREMENT
                    || u.getOperator() == UnaryExpr.Operator.PREFIX_DECREMENT
                    || u.getOperator() == UnaryExpr.Operator.POSTFIX_DECREMENT;
            if (incDec && u.getExpression().isNameExpr()) {
                out.append("\"incdec\",\"name\":").append(str(u.getExpression().asNameExpr().getNameAsString()));
                return;
            }
        }
        out.append("\"other\",\"hasCall\":")
                .append(!expr.findAll(com.github.javaparser.ast.expr.MethodCallExpr.class).isEmpty()
                        || expr.isMethodCallExpr());
    }

    /** An array access whose array is a plain name: {array, index (pure or null)}. */
    private static String read(ArrayAccessExpr access) {
        if (!access.getName().isNameExpr()) {
            return "null";
        }
        return "{\"array\":" + str(access.getName().asNameExpr().getNameAsString())
                + ",\"index\":" + pure(access.getIndex()) + "}";
    }

    /** Like {@link #pure}, but with exactly ONE method call allowed, written
     *  as {"t":"call"}: the call's result is supplied by whoever evaluates
     *  the description (the callee's own derived return value). "null" when
     *  the expression has no call, more than one, or anything impure. */
    static String withOneCall(Expression e) {
        if (e.findAll(MethodCallExpr.class).size() != 1) {
            return "null";
        }
        return callAware(e);
    }

    private static boolean hasCall(Expression e) {
        return e.isMethodCallExpr() || !e.findAll(MethodCallExpr.class).isEmpty();
    }

    private static String callAware(Expression e) {
        if (e.isMethodCallExpr()) {
            return "{\"t\":\"call\",\"name\":" + str(e.asMethodCallExpr().getNameAsString()) + "}";
        }
        if (e.isEnclosedExpr()) {
            return callAware(e.asEnclosedExpr().getInner());
        }
        if (e.isBinaryExpr()) {
            BinaryExpr b = e.asBinaryExpr();
            String left = hasCall(b.getLeft()) ? callAware(b.getLeft()) : pure(b.getLeft());
            String right = hasCall(b.getRight()) ? callAware(b.getRight()) : pure(b.getRight());
            String op = switch (b.getOperator()) {
                case PLUS -> "+";
                case MINUS -> "-";
                case MULTIPLY -> "*";
                case DIVIDE -> "/";
                case REMAINDER -> "%";
                default -> null;
            };
            if (op == null || left.equals("null") || right.equals("null")) {
                return "null";
            }
            return "{\"t\":\"bin\",\"op\":" + str(op) + ",\"l\":" + left + ",\"r\":" + right + "}";
        }
        return "null";
    }

    /** A side-effect-free expression as a small JSON tree, or "null". */
    static String pure(Expression e) {
        if (e.isEnclosedExpr()) {
            return pure(e.asEnclosedExpr().getInner());
        }
        if (e.isNameExpr()) {
            return "{\"t\":\"name\",\"n\":" + str(e.asNameExpr().getNameAsString()) + "}";
        }
        if (e.isIntegerLiteralExpr()) {
            return "{\"t\":\"int\",\"v\":" + e.asIntegerLiteralExpr().asNumber() + "}";
        }
        if (e.isLongLiteralExpr()) {
            return "{\"t\":\"long\",\"v\":" + e.asLongLiteralExpr().asNumber() + "}";
        }
        if (e.isBooleanLiteralExpr()) {
            return "{\"t\":\"bool\",\"v\":" + e.asBooleanLiteralExpr().getValue() + "}";
        }
        if (e.isCharLiteralExpr()) {
            return "{\"t\":\"char\",\"v\":" + str(e.asCharLiteralExpr().asChar() + "") + "}";
        }
        if (e.isStringLiteralExpr()) {
            return "{\"t\":\"str\",\"v\":" + str(e.asStringLiteralExpr().asString()) + "}";
        }
        if (e.isFieldAccessExpr() && e.asFieldAccessExpr().getNameAsString().equals("length")
                && e.asFieldAccessExpr().getScope().isNameExpr()) {
            return "{\"t\":\"len\",\"n\":" + str(e.asFieldAccessExpr().getScope().asNameExpr().getNameAsString()) + "}";
        }
        if (e.isUnaryExpr() && e.asUnaryExpr().getOperator() == UnaryExpr.Operator.MINUS) {
            String inner = pure(e.asUnaryExpr().getExpression());
            return inner.equals("null") ? "null" : "{\"t\":\"neg\",\"e\":" + inner + "}";
        }
        if (e.isBinaryExpr()) {
            BinaryExpr b = e.asBinaryExpr();
            String op = switch (b.getOperator()) {
                case PLUS -> "+";
                case MINUS -> "-";
                case MULTIPLY -> "*";
                case DIVIDE -> "/";
                case REMAINDER -> "%";
                default -> null;
            };
            String left = pure(b.getLeft());
            String right = pure(b.getRight());
            if (op == null || left.equals("null") || right.equals("null")) {
                return "null";
            }
            return "{\"t\":\"bin\",\"op\":" + str(op) + ",\"l\":" + left + ",\"r\":" + right + "}";
        }
        if (e.isArrayAccessExpr()) {
            ArrayAccessExpr a = e.asArrayAccessExpr();
            String index = pure(a.getIndex());
            if (!a.getName().isNameExpr() || index.equals("null")) {
                return "null";
            }
            return "{\"t\":\"idx\",\"a\":" + str(a.getName().asNameExpr().getNameAsString()) + ",\"i\":" + index + "}";
        }
        return "null";
    }

    static String str(String value) {
        StringBuilder sb = new StringBuilder("\"");
        for (char c : value.toCharArray()) {
            switch (c) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                default -> {
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
                }
            }
        }
        return sb.append('"').toString();
    }
}
