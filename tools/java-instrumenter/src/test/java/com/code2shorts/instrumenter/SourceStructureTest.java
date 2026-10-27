package com.code2shorts.instrumenter;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.github.javaparser.StaticJavaParser;
import org.junit.jupiter.api.Test;

/** Phase 8.4: SourceStructure only describes side-effect-free expressions. */
class SourceStructureTest {

    @Test
    void pureExpressionsAreDescribed() {
        assertEquals("{\"t\":\"bin\",\"op\":\"-\",\"l\":{\"t\":\"len\",\"n\":\"a\"},\"r\":{\"t\":\"int\",\"v\":1}}",
                SourceStructure.pure(StaticJavaParser.parseExpression("a.length - 1")));
        assertEquals("{\"t\":\"long\",\"v\":1}", SourceStructure.pure(StaticJavaParser.parseExpression("1L")));
    }

    @Test
    void anythingWithSideEffectsIsRefused() {
        assertEquals("null", SourceStructure.pure(StaticJavaParser.parseExpression("i++")));
        assertEquals("null", SourceStructure.pure(StaticJavaParser.parseExpression("f(x)")));
        assertEquals("null", SourceStructure.pure(StaticJavaParser.parseExpression("x = 3")));
    }

    @Test
    void exactlyOneCallMayBeSubstituted() {
        String described = SourceStructure.withOneCall(StaticJavaParser.parseExpression("n * f(n - 1)"));
        assertTrue(described.contains("{\"t\":\"call\",\"name\":\"f\"}"), described);
        assertEquals("null", SourceStructure.withOneCall(StaticJavaParser.parseExpression("f(1) + g(2)")));
        assertEquals("null", SourceStructure.withOneCall(StaticJavaParser.parseExpression("n + 1")));
    }

    @Test
    void stringsAreEscapedForJson() {
        assertEquals("\"a\\\"b\\\\c\\n\"", SourceStructure.str("a\"b\\c\n"));
    }
}
