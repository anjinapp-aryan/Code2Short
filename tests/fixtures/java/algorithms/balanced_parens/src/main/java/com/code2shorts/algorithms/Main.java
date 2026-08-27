package com.code2shorts.algorithms;

import java.util.ArrayDeque;
import java.util.Deque;

/**
 * Balanced parentheses using an ArrayDeque as a STACK.
 *
 * push/pop act on the deque's head, and the tracer records that end from
 * the observed operation — nothing infers "this is a stack" from the
 * algorithm's name (ADR-6.3).
 */
public final class Main {

    private Main() {}

    public static boolean balanced(String s) {
        Deque<Character> stack = new ArrayDeque<>();
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '(') {
                stack.push(c);
            } else if (c == ')') {
                if (stack.isEmpty()) {
                    return false;
                }
                stack.pop();
            }
        }
        return stack.isEmpty();
    }

    public static void main(String[] args) {
        String input = args.length > 0 ? args[0] : "(())";
        System.out.println(balanced(input));
    }
}
