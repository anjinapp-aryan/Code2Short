package com.code2shorts.reversestring;

// Intentionally never returns, used by Phase 1 failure tests to prove
// execute() detects and reports a timeout instead of hanging forever.
public final class Main {
    public static void main(String[] args) throws InterruptedException {
        while (true) {
            Thread.sleep(1000);
        }
    }
}
