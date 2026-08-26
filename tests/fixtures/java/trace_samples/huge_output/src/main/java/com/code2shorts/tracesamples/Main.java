package com.code2shorts.tracesamples;

public final class Main {
    public static void main(String[] args) {
        // No loop, and the big string is never assigned to a local (so it's
        // never captured into a VARIABLE_ASSIGN event's new_value) — this
        // exercises ONLY the max_output_size limit via the println itself,
        // not max_events/max_loop_iterations or an oversized trace event.
        System.out.println("x".repeat(5_000_000));
    }
}
