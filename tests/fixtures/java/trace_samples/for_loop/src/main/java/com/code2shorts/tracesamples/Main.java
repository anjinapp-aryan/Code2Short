package com.code2shorts.tracesamples;

public final class Main {
    public static void main(String[] args) {
        int[] numbers = {1, 2, 3, 4, 5};
        System.out.println(sumArray(numbers));
    }

    public static int sumArray(int[] values) {
        int total = 0;
        for (int i = 0; i < values.length; i++) {
            int current = values[i];
            total = total + current;
        }
        return total;
    }
}
