package com.code2shorts.algorithms;

public final class Main {
    public static int removeDuplicates(int[] nums) {
        if (nums.length == 0) {
            return 0;
        }
        int slow = 0;
        for (int fast = 1; fast < nums.length; fast++) {
            int current = nums[fast];
            int kept = nums[slow];
            if (current != kept) {
                slow++;
                nums[slow] = current;
            }
        }
        return slow + 1;
    }

    public static void main(String[] args) {
        int[] nums = {1, 1, 2, 2, 3};
        int length = removeDuplicates(nums);
        System.out.println(length);
    }
}
