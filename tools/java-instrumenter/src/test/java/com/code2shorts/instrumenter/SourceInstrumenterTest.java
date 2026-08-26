package com.code2shorts.instrumenter;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import org.junit.jupiter.api.Test;

class SourceInstrumenterTest {

    private CompilationUnit instrument(String source) throws UnsupportedConstructException {
        CompilationUnit unit = StaticJavaParser.parse(source);
        DenylistValidator.validate(unit);
        new SourceInstrumenter(unit.getTypes().get(0).getNameAsString()).instrument(unit);
        return unit;
    }

    @Test
    void wrapsMethodEnterAndExitWithReturnValue() throws Exception {
        String source =
                "public class Adder { public static int add(int a, int b) { return a + b; } }";
        String result = instrument(source).toString();
        assertTrue(result.contains("Code2ShortsTrace.methodEnter(\"Adder.add\""));
        assertTrue(result.contains("Code2ShortsTrace.methodExit(\"Adder.add\""));
    }

    @Test
    void logsVariableDeclarationAssignment() throws Exception {
        String source = "public class C { public static void m() { int x = 5; } }";
        String result = instrument(source).toString();
        // Values go through Code2ShortsTrace.repr(), not String.valueOf():
        // String.valueOf(int[]) returns an identity hash ("[I@7ad041f3")
        // instead of the array's contents. repr() is a set of compile-time
        // resolved overloads that render arrays properly.
        assertTrue(result.contains("Code2ShortsTrace.assign(\"x\", null,"));
        assertTrue(result.contains("Code2ShortsTrace.repr(x)"));
    }

    @Test
    void logsIncrementWithOldAndNewValue() throws Exception {
        String source = "public class C { public static void m() { int x = 0; x++; } }";
        String result = instrument(source).toString();
        assertTrue(result.contains("var __c2s_old_"));
        assertTrue(result.contains("Code2ShortsTrace.assign(\"x\", __c2s_old_"));
    }

    @Test
    void wrapsWhileConditionAndLogsLoopIteration() throws Exception {
        String source =
                "public class C { public static void m() { int i = 0; while (i < 3) { i++; } } }";
        String result = instrument(source).toString();
        assertTrue(result.contains("Code2ShortsTrace.condition(i < 3"));
        assertTrue(result.contains("Code2ShortsTrace.loopIteration("));
    }

    @Test
    void wrapsIfCondition() throws Exception {
        String source =
                "public class C { public static void m(int x) { if (x > 0) { x = 1; } } }";
        String result = instrument(source).toString();
        assertTrue(result.contains("Code2ShortsTrace.condition(x > 0"));
    }

    @Test
    void logsArrayReadAndWrite() throws Exception {
        String source =
                "public class C { public static void m(int[] a) { int x = a[0]; a[1] = x; } }";
        String result = instrument(source).toString();
        assertTrue(result.contains("Code2ShortsTrace.arrayRead(\"a\", 0"));
        assertTrue(result.contains("Code2ShortsTrace.arrayWrite(\"a\", 1"));
    }

    @Test
    void mainMethodGetsProgramStartAndEndWrapper() throws Exception {
        String source =
                "public class C { public static void main(String[] args) { System.out.println(\"hi\"); } }";
        String result = instrument(source).toString();
        assertTrue(result.contains("Code2ShortsTrace.programStart()"));
        assertTrue(result.contains("Code2ShortsTrace.programEnd()"));
        assertTrue(result.contains("Code2ShortsTrace.exceptionThrown(__c2s_t)"));
    }

    @Test
    void rejectsThreadUsage() {
        String source =
                "public class C { public static void m() { Thread t = new Thread(); } }";
        assertThrows(UnsupportedConstructException.class, () -> instrument(source));
    }

    @Test
    void rejectsRandom() {
        String source =
                "import java.util.Random; public class C { public static void m() { Random r = new Random(); } }";
        assertThrows(UnsupportedConstructException.class, () -> instrument(source));
    }

    @Test
    void rejectsSystemCurrentTimeMillis() {
        String source =
                "public class C { public static void m() { long t = System.currentTimeMillis(); } }";
        assertThrows(UnsupportedConstructException.class, () -> instrument(source));
    }

    @Test
    void rejectsLambda() {
        String source =
                "public class C { public static void m() { Runnable r = () -> {}; } }";
        assertThrows(UnsupportedConstructException.class, () -> instrument(source));
    }
}
