package com.code2shorts.tracesamples;

public final class Main {
    public static void main(String[] args) {
        System.out.println(boom());
    }

    public static int boom() {
        int[] tiny = new int[3];
        int value = tiny[5];
        return value;
    }
}
