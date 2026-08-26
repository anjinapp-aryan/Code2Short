package com.code2shorts.reversestring;

public final class Main {
    public static void main(String[] args) {
        String input = args.length > 0 ? args[0] : "";
        System.out.println(ReverseString.reverse(input));
    }
}
