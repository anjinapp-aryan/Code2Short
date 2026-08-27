package com.code2shorts.algorithms;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Two Sum using a Map.
 *
 * Declared as LinkedHashMap deliberately: its iteration order is
 * insertion order BY SPECIFICATION, so the observed entry order is
 * deterministic and semantically meaningful. A plain HashMap also traces
 * correctly, but its order is unspecified by the JLS, so the tracer
 * canonically sorts it and flags the order as non-semantic (ADR-6.4).
 */
public final class Main {

    private Main() {}

    public static int[] twoSum(int[] nums, int target) {
        Map<Integer, Integer> seen = new LinkedHashMap<>();
        for (int i = 0; i < nums.length; i++) {
            int need = target - nums[i];
            if (seen.containsKey(need)) {
                int[] answer = {seen.get(need), i};
                return answer;
            }
            seen.put(nums[i], i);
        }
        int[] none = {-1, -1};
        return none;
    }

    public static void main(String[] args) {
        int[] nums = {2, 7, 11, 15};
        int target = args.length > 0 ? Integer.parseInt(args[0]) : 26;
        int[] result = twoSum(nums, target);
        System.out.println(result[0] + "," + result[1]);
    }
}
