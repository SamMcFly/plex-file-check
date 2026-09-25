import unittest
from plexcheck.cadence import assess_pair


class CadenceTests(unittest.TestCase):
    def test_both_conditions_required(self):
        self.assertTrue(assess_pair(600, 480, 4)[0])
        self.assertFalse(assess_pair(600, 480, 20)[0])
        self.assertFalse(assess_pair(600, 600, 0)[0])

    def test_missing_counts_not_confirmation(self):
        self.assertIsNone(assess_pair(0, 480, 0)[0])
        self.assertIsNone(assess_pair(600, 480, None)[0])
