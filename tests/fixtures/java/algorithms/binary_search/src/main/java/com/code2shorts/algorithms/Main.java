package com.code2shorts.algorithms;

public final class Main {

    private Main() {}

    public static int search(int[] nums, int target) {
        int low = 0;
        int high = nums.length - 1;
        while (low <= high) {
            int mid = (low + high) / 2;
            int value = nums[mid];
            if (value == target) {
                return mid;
            }
            if (value < target) {
                low = mid + 1;
            } else {
                high = mid - 1;
            }
        }
        return -1;
    }

    public static void main(String[] args) {
        int[] nums = {1, 3, 5, 7, 9, 11};
        int target = args.length > 0 ? Integer.parseInt(args[0]) : 9;
        System.out.println(search(nums, target));
    }
}
