import unittest
from find_max import find_max

class TestFindMax(unittest.TestCase):
    def test_empty_list(self):
        self.assertIsNone(find_max([]))
    
    def test_single_element(self):
        self.assertEqual(find_max([42]), 42)
    
    def test_multiple_elements(self):
        self.assertEqual(find_max([1, 2, 3, 4, 5]), 5)
    
    def test_negative_numbers(self):
        self.assertEqual(find_max([-1, -2, -3, -4, -5]), -1)
    
    def test_mixed_numbers(self):
        self.assertEqual(find_max([-1, 0, 1, -2, 2]), 2)

if __name__ == '__main__':
    unittest.main()