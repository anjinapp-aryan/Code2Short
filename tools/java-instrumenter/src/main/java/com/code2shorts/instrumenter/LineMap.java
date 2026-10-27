package com.code2shorts.instrumenter;

import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.NodeList;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.EnumDeclaration;
import com.github.javaparser.ast.body.InitializerDeclaration;
import com.github.javaparser.ast.body.TypeDeclaration;
import com.github.javaparser.ast.expr.StringLiteralExpr;
import com.github.javaparser.ast.stmt.Statement;
import com.github.javaparser.metamodel.PropertyMetaModel;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;

/**
 * Phase 8.4.2: maps every line of the instrumented (re-printed) source back
 * to the line of the user's ORIGINAL source it came from.
 *
 * Why it is needed: the instrumenter re-prints the whole file, so a JVM
 * stack-trace line for an instrumented class is a line of the printed
 * source. Trace events never had this problem (each carries its original
 * line as a literal), but an exception's line comes from the stack trace.
 *
 * How the table is built, without assuming any offset:
 *   1. SourceInstrumenter records the original line of every statement
 *      (user statements from the parse, generated ones where they are made).
 *   2. The instrumented unit is printed, and the printed text is parsed
 *      again. Printing then parsing keeps the statement tree, so the n-th
 *      statement (pre-order, syntax order) of the reparse IS the n-th
 *      statement of the unit; that is checked node by node, never assumed.
 *   3. Each printed statement's line span is filled with its original line,
 *      outer statements first, so the innermost statement wins.
 *
 * The table reaches the runtime as one static initializer per top-level
 * class: `static { Code2ShortsTrace.lineMap("pkg.Type", "0,0,3,..."); }`.
 * It is added before the table is computed, and a string literal is always
 * one printed line, so filling it in moves nothing; the final text is
 * re-checked against the table anyway.
 */
final class LineMap {

    private static final String TRACE = "com.code2shorts.trace.Code2ShortsTrace";

    private LineMap() {}

    /** Registers the line table in `unit` and returns the final source text. */
    static String apply(CompilationUnit unit, Map<Node, Integer> origins) {
        String packagePrefix =
                unit.getPackageDeclaration().map(p -> p.getNameAsString() + ".").orElse("");
        List<StringLiteralExpr> tables = new ArrayList<>();
        for (TypeDeclaration<?> type : unit.getTypes()) {
            boolean canHoldInitializer =
                    (type instanceof ClassOrInterfaceDeclaration declaration && !declaration.isInterface())
                            || type instanceof EnumDeclaration;
            if (!canHoldInitializer) {
                continue; // an interface cannot register; its frames stay unmapped
            }
            InitializerDeclaration initializer = type.addStaticInitializer().getParentNode()
                    .map(InitializerDeclaration.class::cast).orElseThrow();
            initializer.getBody().addStatement(StaticJavaParser.parseStatement(
                    String.format("%s.lineMap(\"%s%s\", \"\");", TRACE, packagePrefix, type.getNameAsString())));
            tables.add(initializer.findFirst(StringLiteralExpr.class, s -> s.getValue().isEmpty()).orElseThrow());
        }

        int[] table = table(unit, unit.toString(), origins);
        String encoded = encode(table);
        for (StringLiteralExpr literal : tables) {
            literal.setValue(encoded);
        }
        String text = unit.toString();
        if (!Arrays.equals(table, table(unit, text, origins))) {
            throw new IllegalStateException("line map moved when it was registered");
        }
        return text;
    }

    static int[] table(CompilationUnit unit, String printed, Map<Node, Integer> origins) {
        List<Statement> ours = statements(unit);
        List<Statement> reparsed = statements(StaticJavaParser.parse(printed));
        if (ours.size() != reparsed.size()) {
            throw new IllegalStateException(
                    "printed source has " + reparsed.size() + " statements, expected " + ours.size());
        }
        int[] table = new int[(int) printed.lines().count() + 2];
        for (int i = 0; i < ours.size(); i++) {
            Statement mine = ours.get(i);
            Statement printedStatement = reparsed.get(i);
            if (mine.getClass() != printedStatement.getClass()) {
                throw new IllegalStateException("statement " + i + " changed kind when printed: "
                        + mine.getClass().getSimpleName() + " vs " + printedStatement.getClass().getSimpleName());
            }
            int origin = originOf(mine, origins);
            if (origin <= 0) {
                continue; // not user code (the registration itself)
            }
            int begin = printedStatement.getBegin().orElseThrow().line;
            int end = printedStatement.getEnd().orElseThrow().line;
            for (int line = begin; line <= end; line++) {
                table[line] = origin; // pre-order: an inner statement overwrites its parent
            }
        }
        return table;
    }

    /** Every statement, pre-order, in SYNTAX order. Node.findAll follows a
     *  node's internal child list, and a child that was replaced or moved
     *  during instrumentation sits at a different place in that list than
     *  where it prints; the metamodel's property order is fixed per node
     *  type, so both trees are walked the same way. */
    private static List<Statement> statements(Node root) {
        List<Statement> out = new ArrayList<>();
        collect(root, out);
        return out;
    }

    private static void collect(Node node, List<Statement> out) {
        if (node instanceof Statement statement) {
            out.add(statement);
        }
        for (PropertyMetaModel property : node.getMetaModel().getAllPropertyMetaModels()) {
            Object value = property.getValue(node);
            if (value instanceof Node child) {
                collect(child, out);
            } else if (value instanceof NodeList<?> children) {
                for (Node child : children) {
                    collect(child, out);
                }
            }
        }
    }

    /** The statement's own origin, else the nearest recorded ancestor's
     *  (an untagged node inside generated code, e.g. a block made only to
     *  hold a single statement). 0 when there is none. */
    private static int originOf(Node node, Map<Node, Integer> origins) {
        for (Node at = node; at != null; at = at.getParentNode().orElse(null)) {
            Integer origin = origins.get(at);
            if (origin != null) {
                return origin;
            }
        }
        return 0;
    }

    private static String encode(int[] table) {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < table.length; i++) {
            if (i > 0) {
                out.append(',');
            }
            out.append(table[i]);
        }
        return out.toString();
    }
}
