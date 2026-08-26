package com.code2shorts.tracesamples;

public final class Main {
    public static void main(String[] args) {
        System.out.println(countPairs(3));
    }

    public static int countPairs(int n) {
        int count = 0;
        int i = 0;
        while (i < n) {
            int j = 0;
            while (j < n) {
                count++;
                j++;
            }
            i++;
        }
        return count;
    }
}
