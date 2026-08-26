package com.code2shorts.reversestring;

// Intentionally broken: swaps only the first and last characters instead of
// walking two pointers to the middle. Used by Phase 1 failure tests to
// prove JUnit rejects a wrong implementation.
public final class ReverseString {
    private ReverseString() {}

    public static String reverse(String s) {
        char[] chars = s.toCharArray();
        if (chars.length > 1) {
            char tmp = chars[0];
            chars[0] = chars[chars.length - 1];
            chars[chars.length - 1] = tmp;
        }
        return new String(chars);
    }
}
