package com.code2shorts.reversestring;

// Intentionally throws to produce a non-zero exit code, used by Phase 1
// failure tests to prove execute() surfaces process failure.
public final class Main {
    public static void main(String[] args) {
        throw new RuntimeException("intentional failure for Phase 1 execute() test");
    }
}
