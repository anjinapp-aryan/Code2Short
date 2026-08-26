package com.code2shorts.tracesamples;

public final class Main {
    public static void main(String[] args) {
        int n = args.length > 0 ? Integer.parseInt(args[0]) : 0;
        System.out.println(factorial(n));
    }

    // No base-case bound other than n <= 1 — deliberately unbounded for
    // large n, used to exercise both moderate recursion (small n) and the
    // max_call_depth trace limit (large n).
    public static long factorial(int n) {
        if (n <= 1) {
            return 1L;
        }
        return n * factorial(n - 1);
    }
}
