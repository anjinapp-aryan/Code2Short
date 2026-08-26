package com.code2shorts.algorithms;

// Brute-force two-pointer form on purpose: Phase 2's supported subset is
// arrays + loops + conditions, and a HashMap solution would neither trace
// nor visualize under the current array-oriented model.
public final class Main {
    public static int[] twoSum(int[] nums, int target) {
        for (int i = 0; i < nums.length; i++) {
            for (int j = i + 1; j < nums.length; j++) {
                int a = nums[i];
                int b = nums[j];
                if (a + b == target) {
                    int[] answer = {i, j};
                    return answer;
                }
            }
        }
        int[] none = {-1, -1};
        return none;
    }

    public static void main(String[] args) {
        int[] nums = {2, 7, 11, 15};
        int target = args.length > 0 ? Integer.parseInt(args[0]) : 9;
        int[] result = twoSum(nums, target);
        System.out.println(result[0] + "," + result[1]);
    }
}
