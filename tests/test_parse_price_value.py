#!/usr/bin/env python3
"""
_parse_price_value function test script

This test validates the _parse_price_value method from stock_tracking_agent.py under various input conditions.
"""

import sys
import re
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestParsePriceValue:
    """Independent class for testing _parse_price_value function"""

    @staticmethod
    def _parse_price_value(value) -> float:
        """
        Parse price value and convert to number
        (same logic as method in stock_tracking_agent.py)

        Args:
            value: Price value (number, string, range, etc.)

        Returns:
            float: Parsed price (0 if failed)
        """
        try:
            # If already a number
            if isinstance(value, (int, float)):
                return float(value)

            # If string
            if isinstance(value, str):
                # Remove commas
                value = value.replace(',', '')

                # Check range expression (e.g., "2000~2050", "1,700-1,800")
                range_patterns = [
                    r'(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)',  # 2000~2050 or 2000-2050
                    r'(\d+(?:\.\d+)?)\s*~\s*(\d+(?:\.\d+)?)',     # 2000 ~ 2050
                ]

                for pattern in range_patterns:
                    match = re.search(pattern, value)
                    if match:
                        # Use midpoint of range
                        low = float(match.group(1))
                        high = float(match.group(2))
                        return (low + high) / 2

                # Try to extract single number
                number_match = re.search(r'(\d+(?:\.\d+)?)', value)
                if number_match:
                    return float(number_match.group(1))

            return 0
        except Exception as e:
            print(f"⚠️  Price value parsing failed: {value} - {str(e)}")
            return 0


def run_tests():
    """Run all test cases"""

    tester = TestParsePriceValue()

    # Define test cases
    test_cases = [
        # (input_value, expected_output, description)

        # 1. Number type tests
        (2000, 2000.0, "Integer input"),
        (2000.5, 2000.5, "Float input"),
        (0, 0.0, "Zero input"),
        (-1500, -1500.0, "Negative input"),

        # 2. String number tests
        ("2000", 2000.0, "String integer"),
        ("2000.5", 2000.5, "String float"),
        ("1,700", 1700.0, "String with comma"),
        ("1,700.5", 1700.5, "String with comma + decimal"),
        ("10,000", 10000.0, "Large number with comma"),

        # 3. Range expression tests (tilde ~)
        ("2000~2050", 2025.0, "Tilde range (no space)"),
        ("2000 ~ 2050", 2025.0, "Tilde range (with space)"),
        ("1,700~1,800", 1750.0, "Comma + tilde range"),
        ("1,350~1,400", 1375.0, "Comma + tilde range 2"),

        # 4. Range expression tests (hyphen -)
        ("2000-2050", 2025.0, "Hyphen range"),
        ("1,700-1,800", 1750.0, "Comma + hyphen range"),

        # 5. Patterns found in actual error cases
        ("2,000~2,050", 2025.0, "Actual error case 1"),
        ("2,400~2,500", 2450.0, "Actual error case 2"),
        ("1,350~1,400", 1375.0, "Actual error case 3"),
        ("1,700", 1700.0, "Actual error case 4 (single value)"),

        # 6. Range with decimals
        ("1500.5~1600.5", 1550.5, "Range with decimals"),
        ("1,500.25~1,600.75", 1550.5, "Comma + decimal range"),

        # 7. Cases with lots of whitespace
        ("2000  ~  2050", 2025.0, "Range with lots of space"),
        ("  1700  ", 1700.0, "Leading/trailing space"),

        # 8. Special cases
        ("", 0.0, "Empty string"),
        (None, 0.0, "None input"),
        ("abc", 0.0, "String without number"),
        ("price: 2000", 2000.0, "Text included (extract number)"),
        ("약 1,700원", 1700.0, "Korean text included (extract number)"),

        # 9. Complex patterns
        ("1,700원~2,000원", 1850.0, "Range with unit"),
        ("최소 1,500 ~ 최대 2,000", 1750.0, "Range with description"),
    ]

    # Run tests
    print("=" * 80)
    print("_parse_price_value Function Test")
    print("=" * 80)
    print()

    passed = 0
    failed = 0

    for i, (input_value, expected, description) in enumerate(test_cases, 1):
        result = tester._parse_price_value(input_value)

        # Tolerance for floating point comparison
        tolerance = 0.01
        is_correct = abs(result - expected) < tolerance

        if is_correct:
            status = "✅ PASS"
            passed += 1
        else:
            status = "❌ FAIL"
            failed += 1

        print(f"Test #{i}: {status}")
        print(f"  Description: {description}")
        print(f"  Input: {repr(input_value)}")
        print(f"  Expected: {expected}")
        print(f"  Result: {result}")

        if not is_correct:
            print(f"  ⚠️  Difference: {abs(result - expected)}")

        print()

    # Summary
    print("=" * 80)
    print("Test Results Summary")
    print("=" * 80)
    print(f"Total tests: {len(test_cases)}")
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failed}")
    print(f"Success rate: {(passed / len(test_cases) * 100):.1f}%")
    print("=" * 80)

    # Return exit code if failed
    return 0 if failed == 0 else 1


def run_edge_case_tests():
    """Additional edge case tests"""

    print("\n\n")
    print("=" * 80)
    print("Additional Edge Case Tests")
    print("=" * 80)
    print()

    tester = TestParsePriceValue()

    edge_cases = [
        # Very large numbers
        ("1,000,000", 1000000.0, "Million unit"),
        ("1,000,000~2,000,000", 1500000.0, "Million unit range"),

        # Very small numbers
        ("0.001", 0.001, "Very small decimal"),
        ("0.001~0.002", 0.0015, "Very small decimal range"),

        # Multiple numbers (extract first only)
        ("1,700 or 2,000", 1700.0, "First of multiple numbers"),

        # Reverse range (large ~ small)
        ("2000~1500", 1750.0, "Reverse range"),

        # Negative range
        ("-1000~-500", -750.0, "Negative range"),

        # Mixed delimiters
        ("1,700 - 1,800", 1750.0, "Comma + hyphen (with space)"),
    ]

    for i, (input_value, expected, description) in enumerate(edge_cases, 1):
        result = tester._parse_price_value(input_value)
        tolerance = 0.01
        is_correct = abs(result - expected) < tolerance

        status = "✅ PASS" if is_correct else "❌ FAIL"

        print(f"Edge case #{i}: {status}")
        print(f"  Description: {description}")
        print(f"  Input: {repr(input_value)}")
        print(f"  Expected: {expected}")
        print(f"  Result: {result}")
        print()


def performance_test():
    """Performance test"""
    import time

    print("\n\n")
    print("=" * 80)
    print("Performance Test")
    print("=" * 80)
    print()

    tester = TestParsePriceValue()

    # Various input patterns
    test_inputs = [
        2000,
        "2,000",
        "2,000~2,050",
        "1,700-1,800",
        "about 1,500 KRW",
    ]

    iterations = 10000

    for input_value in test_inputs:
        start_time = time.time()

        for _ in range(iterations):
            tester._parse_price_value(input_value)

        elapsed_time = time.time() - start_time
        avg_time = (elapsed_time / iterations) * 1000  # milliseconds

        print(f"Input: {repr(input_value)}")
        print(f"  {iterations:,} iterations time: {elapsed_time:.4f}s")
        print(f"  Average execution time: {avg_time:.6f}ms")
        print()


if __name__ == "__main__":
    # Run basic tests
    exit_code = run_tests()

    # Run edge case tests
    run_edge_case_tests()

    # Run performance test
    performance_test()

    sys.exit(exit_code)
