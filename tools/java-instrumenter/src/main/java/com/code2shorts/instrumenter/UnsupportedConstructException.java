package com.code2shorts.instrumenter;

/** Thrown when the input source uses a construct Phase 2 does not support —
 *  either for determinism (threads, Random, wall-clock/env access) or
 *  because it's simply out of scope (lambdas, streams, reflection). Always
 *  maps to ExecutionTrace.status == INSTRUMENTATION_FAILED on the Python
 *  side; never allowed to produce a partial/fabricated trace. */
public final class UnsupportedConstructException extends Exception {
    public UnsupportedConstructException(String message) {
        super(message);
    }
}
