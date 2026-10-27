package com.code2shorts.instrumenter;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import java.util.List;
import org.junit.jupiter.api.Test;

/** Phase 8.4.2: printed (instrumented) line -> original line. */
class LineMapTest {

    private static final String SOURCE = String.join("\n",
            "package demo;",                          // 1
            "",                                       // 2
            "public class Demo {",                    // 3
            "    public static void main(String[] args) {", // 4
            "        int[] a = new int[2];",          // 5
            "        int x = 1;",                     // 6
            "        x = x + 1;",                     // 7
            "        a[x] = 3;",                      // 8
            "    }",                                  // 9
            "",                                       // 10
            "    static int pick(int[] a, int i) {",  // 11
            "        return a[i];",                   // 12
            "    }",                                  // 13
            "}");

    private static String instrumented() {
        CompilationUnit unit = StaticJavaParser.parse(SOURCE);
        SourceInstrumenter instrumenter = new SourceInstrumenter("Demo");
        instrumenter.instrument(unit);
        return LineMap.apply(unit, instrumenter.origins());
    }

    private static int[] registeredTable(String text) {
        String marker = "Code2ShortsTrace.lineMap(\"demo.Demo\", \"";
        int start = text.indexOf(marker) + marker.length();
        String[] parts = text.substring(start, text.indexOf('"', start)).split(",");
        int[] table = new int[parts.length];
        for (int i = 0; i < parts.length; i++) {
            table[i] = Integer.parseInt(parts[i]);
        }
        return table;
    }

    private static int printedLineOf(String text, String fragment) {
        List<String> lines = text.lines().toList();
        for (int i = 0; i < lines.size(); i++) {
            if (lines.get(i).contains(fragment)) {
                return i + 1;
            }
        }
        throw new AssertionError("not printed: " + fragment);
    }

    @Test
    void userStatementsMapToTheirOriginalLines() {
        String text = instrumented();
        int[] table = registeredTable(text);
        assertEquals(8, table[printedLineOf(text, "a[x] = 3;")]);
        assertEquals(7, table[printedLineOf(text, "x = x + 1;")]);
    }

    @Test
    void generatedStatementsMapToTheStatementTheyWereGeneratedFor() {
        String text = instrumented();
        int[] table = registeredTable(text);
        // The return was rewritten into `var __c2s_ret_N = a[i];` - user code
        // now inside a generated statement.
        assertEquals(12, table[printedLineOf(text, "= a[i];")]);
        assertEquals(8, table[printedLineOf(text, "arrayWrite(\"a\"")]);
    }

    @Test
    void theShiftIsNotConstant() {
        String text = instrumented();
        int shiftMain = printedLineOf(text, "a[x] = 3;") - 8;
        int shiftPick = printedLineOf(text, "= a[i];") - 12;
        assertTrue(shiftMain != shiftPick, "shifts " + shiftMain + " and " + shiftPick);
    }

    @Test
    void theRegistrationItselfIsNotUserCode() {
        String text = instrumented();
        assertEquals(0, registeredTable(text)[printedLineOf(text, "Code2ShortsTrace.lineMap(")]);
    }
}
