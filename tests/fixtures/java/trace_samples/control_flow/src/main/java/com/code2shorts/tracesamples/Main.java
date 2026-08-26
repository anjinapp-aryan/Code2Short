package com.code2shorts.tracesamples;

public final class Main {
    public static void main(String[] args) {
        int n = args.length > 0 ? Integer.parseInt(args[0]) : 0;
        System.out.println(classify(n));
    }

    public static String classify(int n) {
        String result;
        if (n > 0) {
            result = "positive";
        } else if (n < 0) {
            result = "negative";
        } else {
            result = "zero";
        }
        return result;
    }
}
