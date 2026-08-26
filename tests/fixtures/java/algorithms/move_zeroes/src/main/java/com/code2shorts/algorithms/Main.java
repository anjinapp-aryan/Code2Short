package com.code2shorts.algorithms;

public final class Main {
    public static int[] moveZeroes(int[] nums) {
        int slow = 0;
        for (int fast = 0; fast < nums.length; fast++) {
            int value = nums[fast];
            if (value != 0) {
                int temp = nums[slow];
                nums[slow] = value;
                nums[fast] = temp;
                slow++;
            }
        }
        return nums;
    }

    public static void main(String[] args) {
        int[] nums = {0, 1, 0, 3, 12};
        int[] result = moveZeroes(nums);
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < result.length; i++) {
            if (i > 0) {
                out.append(",");
            }
            out.append(result[i]);
        }
        System.out.println(out.toString());
    }
}
