import unittest


class ReportImportTest(unittest.TestCase):
    def test_report_module_imports(self):
        import src.report  # noqa: F401


if __name__ == "__main__":
    unittest.main()
