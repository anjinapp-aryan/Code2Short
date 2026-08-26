package com.code2shorts.trace;

import java.io.PrintStream;

/**
 * Trusted Code2Shorts trace runtime. Not LLM-generated, not user-authored —
 * materialized verbatim into every traced workspace, same trust level as
 * the generated pom.xml. Instrumented source calls these static methods;
 * this class enforces trace limits IN-PROCESS (see checkX methods) so an
 * infinite loop or runaway recursion terminates via a thrown
 * TraceLimitExceededException, not only via the wall-clock timeout.
 *
 * Emits one JSON line per event to stdout, prefixed "TRACE:" so the Python
 * side can separate trace events from the traced program's own output
 * (System.out.println calls the program legitimately makes).
 */
public final class Code2ShortsTrace {

    private Code2ShortsTrace() {}

    public static final class TraceLimitExceededException extends RuntimeException {
        public TraceLimitExceededException(String message) {
            super(message);
        }
    }

    private static final int MAX_EVENTS = readIntEnv("C2S_MAX_EVENTS", 2000);
    private static final int MAX_LOOP_ITERATIONS = readIntEnv("C2S_MAX_LOOP_ITERATIONS", 1000);
    private static final int MAX_CALL_DEPTH = readIntEnv("C2S_MAX_CALL_DEPTH", 200);
    private static final long MAX_OUTPUT_BYTES = readLongEnv("C2S_MAX_OUTPUT_BYTES", 1_000_000L);

    // Captured at class-load time, BEFORE programStart() ever wraps
    // System.out — trace lines always go through this real, unwrapped
    // stream, never the counting wrapper below. Otherwise a program that
    // has already exhausted its output budget could never report the
    // TraceLimitExceededException that budget itself raised: emitting that
    // one EXCEPTION_THROWN line would immediately re-throw, and the real
    // failure reason would be lost.
    private static final PrintStream TRACE_OUT = System.out;

    private static int eventCounter = 0;
    private static int loopIterationCounter = 0;
    private static int callDepth = 0;

    private static int readIntEnv(String name, int fallback) {
        String value = System.getenv(name);
        return value == null ? fallback : Integer.parseInt(value);
    }

    private static long readLongEnv(String name, long fallback) {
        String value = System.getenv(name);
        return value == null ? fallback : Long.parseLong(value);
    }

    public static void programStart() {
        System.setOut(new CountingPrintStream(System.out, MAX_OUTPUT_BYTES));
        JsonWriter json = new JsonWriter();
        json.field("event_type", "PROGRAM_START");
        json.field("call_depth", callDepth);
        json.field("description", "program started");
        emit(json);
    }

    public static void programEnd() {
        JsonWriter json = new JsonWriter();
        json.field("event_type", "PROGRAM_END");
        json.field("call_depth", callDepth);
        json.field("description", "program ended");
        emit(json);
    }

    public static void methodEnter(String method, int line) {
        callDepth++;
        checkCallDepth();
        JsonWriter json = new JsonWriter();
        json.field("event_type", "METHOD_ENTER");
        json.field("line_number", line);
        json.field("call_depth", callDepth);
        json.field("method", method);
        json.field("description", "enter " + method);
        emit(json);
    }

    public static void methodExit(String method, String returnValue, int line) {
        JsonWriter json = new JsonWriter();
        json.field("event_type", "METHOD_EXIT");
        json.field("line_number", line);
        json.field("call_depth", callDepth);
        json.field("method", method);
        if (returnValue != null) {
            json.field("return_value", returnValue);
        }
        json.field("description", "exit " + method);
        emit(json);
        callDepth--;
    }

    /** oldValue == null means this is the variable's initial declaration. */
    public static void assign(String name, String oldValue, String newValue, int line) {
        JsonWriter json = new JsonWriter();
        json.field("event_type", "VARIABLE_ASSIGN");
        json.field("line_number", line);
        json.field("call_depth", callDepth);
        json.field("variable_name", name);
        if (oldValue != null) {
            json.field("old_value", oldValue);
        }
        json.field("new_value", newValue);
        json.field("description", name + " = " + newValue);
        emit(json);
    }

    /** Pass-through wrapper: returns `result` unchanged so it can wrap any
     *  boolean expression in place (if/while/for conditions) without
     *  altering control flow. */
    public static boolean condition(boolean result, String exprText, int line) {
        JsonWriter json = new JsonWriter();
        json.field("event_type", "CONDITION_EVALUATED");
        json.field("line_number", line);
        json.field("call_depth", callDepth);
        json.field("condition_result", result);
        json.field("description", exprText + " -> " + result);
        emit(json);
        return result;
    }

    public static void loopIteration(int line) {
        loopIterationCounter++;
        checkLoopIterations();
        JsonWriter json = new JsonWriter();
        json.field("event_type", "LOOP_ITERATION");
        json.field("line_number", line);
        json.field("call_depth", callDepth);
        json.field("iteration", loopIterationCounter);
        json.field("description", "iteration " + loopIterationCounter);
        emit(json);
    }

    public static void arrayRead(String arrayName, int index, String value, int line) {
        JsonWriter json = new JsonWriter();
        json.field("event_type", "ARRAY_READ");
        json.field("line_number", line);
        json.field("call_depth", callDepth);
        json.field("variable_name", arrayName + "[" + index + "]");
        json.field("new_value", value);
        json.field("description", arrayName + "[" + index + "] -> " + value);
        emit(json);
    }

    public static void arrayWrite(String arrayName, int index, String value, int line) {
        JsonWriter json = new JsonWriter();
        json.field("event_type", "ARRAY_WRITE");
        json.field("line_number", line);
        json.field("call_depth", callDepth);
        json.field("variable_name", arrayName + "[" + index + "]");
        json.field("new_value", value);
        json.field("description", arrayName + "[" + index + "] = " + value);
        emit(json);
    }

    /** Called from main's catch(Throwable) block. Not counted against
     *  MAX_EVENTS / not subject to checkEvents() — a program that hit its
     *  event limit must still be able to report the exception that limit
     *  itself raised. */
    public static void exceptionThrown(Throwable t) {
        StackTraceElement site = t.getStackTrace().length > 0 ? t.getStackTrace()[0] : null;
        JsonWriter json = new JsonWriter();
        json.field("event_type", "EXCEPTION_THROWN");
        json.field("call_depth", callDepth);
        json.field("exception_class", t.getClass().getName());
        json.field("message", String.valueOf(t.getMessage()));
        if (site != null) {
            json.field("thrown_at_line", site.getLineNumber());
            json.field("thrown_in_method", site.getMethodName());
        }
        json.field("description", t.getClass().getSimpleName() + ": " + t.getMessage());
        TRACE_OUT.println("TRACE:" + json.build());
    }

    private static void checkCallDepth() {
        if (callDepth > MAX_CALL_DEPTH) {
            throw new TraceLimitExceededException("max_call_depth exceeded: " + MAX_CALL_DEPTH);
        }
    }

    private static void checkLoopIterations() {
        if (loopIterationCounter > MAX_LOOP_ITERATIONS) {
            throw new TraceLimitExceededException(
                    "max_loop_iterations exceeded: " + MAX_LOOP_ITERATIONS);
        }
    }

    private static void checkEvents() {
        if (eventCounter > MAX_EVENTS) {
            throw new TraceLimitExceededException("max_events exceeded: " + MAX_EVENTS);
        }
    }

    private static void emit(JsonWriter json) {
        eventCounter++;
        checkEvents();
        TRACE_OUT.println("TRACE:" + json.build());
    }

    /** Minimal dependency-free JSON object writer — no JSON library is added
     *  to the traced workspace's classpath; this is the only thing that
     *  needs to serialize, and the schema is small and fixed. */
    private static final class JsonWriter {
        private final StringBuilder out = new StringBuilder("{");
        private boolean first = true;

        void field(String key, String value) {
            comma();
            out.append('"').append(key).append("\":\"").append(escape(value)).append('"');
        }

        void field(String key, int value) {
            comma();
            out.append('"').append(key).append("\":").append(value);
        }

        void field(String key, boolean value) {
            comma();
            out.append('"').append(key).append("\":").append(value);
        }

        private void comma() {
            if (!first) {
                out.append(',');
            }
            first = false;
        }

        String build() {
            return out.append('}').toString();
        }

        private static String escape(String s) {
            if (s == null) {
                return "";
            }
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < s.length(); i++) {
                char c = s.charAt(i);
                switch (c) {
                    case '\\':
                        sb.append("\\\\");
                        break;
                    case '"':
                        sb.append("\\\"");
                        break;
                    case '\n':
                        sb.append("\\n");
                        break;
                    case '\r':
                        break;
                    case '\t':
                        sb.append("\\t");
                        break;
                    default:
                        if (c < 0x20) {
                            sb.append(String.format("\\u%04x", (int) c));
                        } else {
                            sb.append(c);
                        }
                }
            }
            return sb.toString();
        }
    }

    /** Enforces max_output_size IN-PROCESS by counting bytes written to
     *  stdout (both trace lines and the program's own output share this
     *  budget). Chosen over adding pipe-size-limiting to
     *  execution.sandbox.run_subprocess, which uses Popen.communicate() —
     *  an all-or-nothing read that cannot safely cap mid-stream without a
     *  threaded/non-blocking rewrite. Enforcing here keeps every trace
     *  limit (events/loop iterations/call depth/output size) using the
     *  same in-JVM mechanism. */
    private static final class CountingPrintStream extends PrintStream {
        private final long maxBytes;
        private long count = 0;

        CountingPrintStream(PrintStream original, long maxBytes) {
            super(original, true);
            this.maxBytes = maxBytes;
        }

        @Override
        public void write(byte[] buf, int off, int len) {
            count += len;
            if (count > maxBytes) {
                // A single println of a large string is chunked into
                // several ~8KB writes by PrintStream's internal buffering,
                // so the limit is often crossed mid-line, not at a line
                // boundary — the caller's earlier chunks already reached
                // the real stream with no trailing newline. Terminate that
                // dangling partial line before throwing, or it silently
                // merges with the next TRACE: line and corrupts the
                // program-output/trace-event line framing downstream.
                super.write('\n');
                super.flush();
                throw new TraceLimitExceededException("max_output_size exceeded: " + maxBytes);
            }
            super.write(buf, off, len);
        }
    }
}
