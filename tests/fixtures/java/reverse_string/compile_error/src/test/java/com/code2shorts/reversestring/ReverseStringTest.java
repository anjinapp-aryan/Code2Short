package com.code2shorts.reversestring;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class ReverseStringTest {

    @Test
    void reversesNormalInput() {
        assertEquals("OLLEH", ReverseString.reverse("HELLO"));
    }

    @Test
    void palindromeInputIsUnchanged() {
        assertEquals("RACECAR", ReverseString.reverse("RACECAR"));
    }

    @Test
    void emptyStringStaysEmpty() {
        assertEquals("", ReverseString.reverse(""));
    }

    @Test
    void singleCharacterIsUnchanged() {
        assertEquals("A", ReverseString.reverse("A"));
    }

    @Test
    void duplicateCharactersReverseCorrectly() {
        assertEquals("SSIM", ReverseString.reverse("MISS"));
    }

    @Test
    void alreadyReversedInputReversesBack() {
        assertEquals("HELLO", ReverseString.reverse("OLLEH"));
    }
}
