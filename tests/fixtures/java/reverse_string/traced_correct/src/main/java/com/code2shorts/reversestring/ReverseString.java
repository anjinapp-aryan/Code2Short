package com.code2shorts.reversestring;

import com.code2shorts.trace.Code2ShortsTrace;

// Hand-instrumented (Step 2 proof only — Step 3+ generates this
// automatically via the AST instrumenter). Mirrors the plain
// tests/fixtures/java/reverse_string/correct implementation exactly, with
// Code2ShortsTrace calls inserted around each observable event.
public final class ReverseString {
    private ReverseString() {}

    public static String reverse(String s) {
        Code2ShortsTrace.methodEnter("ReverseString.reverse", 12);
        char[] chars = s.toCharArray();
        int left = 0;
        Code2ShortsTrace.assign("left", null, String.valueOf(left), 15);
        int right = chars.length - 1;
        Code2ShortsTrace.assign("right", null, String.valueOf(right), 16);
        while (Code2ShortsTrace.condition(left < right, "left < right", 17)) {
            Code2ShortsTrace.loopIteration(17);
            char tmp = chars[left];
            Code2ShortsTrace.arrayRead("chars", left, String.valueOf(tmp), 18);
            char rightValue = chars[right];
            Code2ShortsTrace.arrayRead("chars", right, String.valueOf(rightValue), 19);
            chars[left] = rightValue;
            Code2ShortsTrace.arrayWrite("chars", left, String.valueOf(rightValue), 19);
            chars[right] = tmp;
            Code2ShortsTrace.arrayWrite("chars", right, String.valueOf(tmp), 20);
            int oldLeft = left;
            left++;
            Code2ShortsTrace.assign("left", String.valueOf(oldLeft), String.valueOf(left), 21);
            int oldRight = right;
            right--;
            Code2ShortsTrace.assign("right", String.valueOf(oldRight), String.valueOf(right), 22);
        }
        String result = new String(chars);
        Code2ShortsTrace.methodExit("ReverseString.reverse", result, 24);
        return result;
    }
}
