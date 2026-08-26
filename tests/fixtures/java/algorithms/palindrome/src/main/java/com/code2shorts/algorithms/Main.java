package com.code2shorts.algorithms;

public final class Main {
    public static boolean isPalindrome(char[] chars) {
        int left = 0;
        int right = chars.length - 1;
        while (left < right) {
            char a = chars[left];
            char b = chars[right];
            if (a != b) {
                return false;
            }
            left++;
            right--;
        }
        return true;
    }

    public static void main(String[] args) {
        String input = args.length > 0 ? args[0] : "";
        char[] chars = input.toCharArray();
        System.out.println(isPalindrome(chars));
    }
}
