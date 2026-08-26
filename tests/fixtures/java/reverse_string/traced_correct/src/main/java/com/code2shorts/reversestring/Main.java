package com.code2shorts.reversestring;

import com.code2shorts.trace.Code2ShortsTrace;

public final class Main {
    public static void main(String[] args) {
        Code2ShortsTrace.programStart();
        boolean failed = false;
        try {
            String input = args.length > 0 ? args[0] : "";
            System.out.println(ReverseString.reverse(input));
        } catch (Throwable t) {
            Code2ShortsTrace.exceptionThrown(t);
            failed = true;
        } finally {
            Code2ShortsTrace.programEnd();
        }
        if (failed) {
            System.exit(1);
        }
    }
}
