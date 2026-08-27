package com.code2shorts.algorithms;

import java.util.ArrayDeque;
import java.util.Deque;

/**
 * FIFO queue drain, using the SAME ArrayDeque class as balanced_parens.
 *
 * This fixture exists to prove one SequenceSnapshot serves both uses:
 * here `offer`/`poll` act on opposite ends, so the observed active end is
 * the tail on insert and the head on removal. Nothing in the renderer
 * knows this program is "a queue" — the orientation follows the observed
 * operation (ADR-6.3).
 */
public final class Main {

    private Main() {}

    public static int drain(int count) {
        Deque<Integer> queue = new ArrayDeque<>();
        for (int i = 1; i <= count; i++) {
            queue.offer(i);
        }
        int total = 0;
        while (!queue.isEmpty()) {
            int head = queue.poll();
            total = total + head;
        }
        return total;
    }

    public static void main(String[] args) {
        int count = args.length > 0 ? Integer.parseInt(args[0]) : 3;
        System.out.println(drain(count));
    }
}
