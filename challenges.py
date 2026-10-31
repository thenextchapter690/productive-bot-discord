"""Curated rotation of classic coding-interview style problems for the daily challenge."""
from __future__ import annotations

from datetime import date

# (title, leetcode slug, difficulty, topics, one-line summary written for this bot)
CHALLENGES = [
    ("Two Sum", "two-sum", "Easy", "arrays, hash map",
     "Find two numbers in a list that add up to a target and return their positions."),
    ("Valid Parentheses", "valid-parentheses", "Easy", "stack, strings",
     "Decide whether a string of brackets is opened and closed in the right order."),
    ("Merge Two Sorted Lists", "merge-two-sorted-lists", "Easy", "linked list",
     "Combine two sorted linked lists into one sorted list."),
    ("Best Time to Buy and Sell Stock", "best-time-to-buy-and-sell-stock", "Easy", "arrays, greedy",
     "Given daily prices, find the best profit from one buy followed by one sell."),
    ("Valid Palindrome", "valid-palindrome", "Easy", "two pointers, strings",
     "Check whether a sentence reads the same backwards, ignoring punctuation and case."),
    ("Invert Binary Tree", "invert-binary-tree", "Easy", "trees, recursion",
     "Mirror a binary tree by swapping the left and right children everywhere."),
    ("Valid Anagram", "valid-anagram", "Easy", "hash map, strings",
     "Check whether one word is a rearrangement of another."),
    ("Binary Search", "binary-search", "Easy", "binary search",
     "Find a value in a sorted array in logarithmic time."),
    ("Climbing Stairs", "climbing-stairs", "Easy", "dynamic programming",
     "Count the distinct ways to climb a staircase taking one or two steps at a time."),
    ("Contains Duplicate", "contains-duplicate", "Easy", "arrays, hash set",
     "Report whether any value appears more than once in an array."),
    ("Linked List Cycle", "linked-list-cycle", "Easy", "linked list, two pointers",
     "Detect whether a linked list loops back on itself."),
    ("Reverse Linked List", "reverse-linked-list", "Easy", "linked list",
     "Reverse a singly linked list, iteratively and recursively."),
    ("Maximum Subarray", "maximum-subarray", "Medium", "arrays, dynamic programming",
     "Find the contiguous slice of an array with the largest sum."),
    ("Product of Array Except Self", "product-of-array-except-self", "Medium", "arrays, prefix sums",
     "For each position, compute the product of all other elements without using division."),
    ("Group Anagrams", "group-anagrams", "Medium", "hash map, strings",
     "Group words that are anagrams of each other."),
    ("Top K Frequent Elements", "top-k-frequent-elements", "Medium", "heap, hash map",
     "Return the k values that occur most often in an array."),
    ("Longest Substring Without Repeating Characters", "longest-substring-without-repeating-characters", "Medium",
     "sliding window", "Find the longest stretch of a string with no repeated character."),
    ("Container With Most Water", "container-with-most-water", "Medium", "two pointers",
     "Pick two vertical lines that hold the most water between them."),
    ("3Sum", "3sum", "Medium", "two pointers, sorting",
     "Find all unique triples in an array that add up to zero."),
    ("Number of Islands", "number-of-islands", "Medium", "graphs, DFS/BFS",
     "Count the connected groups of land in a grid."),
    ("Coin Change", "coin-change", "Medium", "dynamic programming",
     "Find the fewest coins needed to make a given amount."),
    ("House Robber", "house-robber", "Medium", "dynamic programming",
     "Maximize loot from a row of houses when you cannot rob two neighbours."),
    ("Merge Intervals", "merge-intervals", "Medium", "sorting, intervals",
     "Merge all overlapping intervals in a list."),
    ("Longest Consecutive Sequence", "longest-consecutive-sequence", "Medium", "hash set",
     "Find the longest run of consecutive integers in an unsorted array in linear time."),
    ("Min Stack", "min-stack", "Medium", "stack, design",
     "Design a stack that returns its minimum element in constant time."),
    ("Validate Binary Search Tree", "validate-binary-search-tree", "Medium", "trees, recursion",
     "Check that a binary tree really satisfies the search-tree ordering rule."),
    ("LRU Cache", "lru-cache", "Medium", "design, hash map, linked list",
     "Design a fixed-size cache that evicts the least recently used entry."),
    ("Word Search", "word-search", "Medium", "backtracking, grid",
     "Decide whether a word can be traced through adjacent cells of a letter grid."),
    ("Course Schedule", "course-schedule", "Medium", "graphs, topological sort",
     "Given course prerequisites, decide whether all courses can be completed."),
    ("Rotate Image", "rotate-image", "Medium", "matrix",
     "Rotate an n by n matrix 90 degrees in place."),
]

DIFFICULTY_EMOJI = {"Easy": "🟢", "Medium": "🟡", "Hard": "🔴"}


def challenge_for(day: date) -> dict[str, str]:
    title, slug, difficulty, topics, summary = CHALLENGES[day.toordinal() % len(CHALLENGES)]
    return {
        "title": title,
        "url": f"https://leetcode.com/problems/{slug}/",
        "difficulty": difficulty,
        "emoji": DIFFICULTY_EMOJI.get(difficulty, "⚪"),
        "topics": topics,
        "summary": summary,
    }
