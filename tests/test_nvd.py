import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "lib"))

from nvd import ChangeCounts, count_changes, format_change_summary, format_diff


class NvdChangeCountTests(unittest.TestCase):
    def test_counts_each_package_change_type_in_plain_output(self):
        changes = count_changes(
            "[A.] added\n[R.] removed\n[U.] upgraded\n[D.] downgraded\n[C.] changed\n"
        )

        self.assertEqual(
            changes,
            ChangeCounts(
                added=1,
                removed=1,
                upgraded=1,
                downgraded=1,
                changed=1,
            ),
        )
        self.assertEqual(changes.total, 5)

    def test_counts_package_changes_in_colored_output(self):
        changes = count_changes("\033[32m[U*]\033[0m package 1 -> 2")

        self.assertEqual(changes.upgraded, 1)
        self.assertEqual(changes.total, 1)

    def test_non_status_brackets_do_not_count_as_package_changes(self):
        changes = count_changes("configuration changed [old-value] -> [new-value]")

        self.assertEqual(changes.total, 0)


class NvdFormattingTests(unittest.TestCase):
    def test_formats_config_only_changes(self):
        self.assertEqual(
            format_change_summary(ChangeCounts()),
            "Config changes found",
        )

    def test_formats_package_change_summary(self):
        changes = ChangeCounts(added=1, removed=2, upgraded=3)

        self.assertEqual(
            format_change_summary(changes),
            "6 package changes: 1 added, 2 removed, 3 upgraded",
        )

    def test_removes_the_two_line_nvd_header_without_mutating_input(self):
        output = "Comparing system closures\nPackages\n[A.] package\n"

        self.assertEqual(format_diff(output), "[A.] package\n")
        self.assertEqual(output, "Comparing system closures\nPackages\n[A.] package\n")


if __name__ == "__main__":
    unittest.main()
