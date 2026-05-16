import unittest


class CatTrackerTest(unittest.TestCase):
    def test_selects_largest_detection_without_prior_target(self):
        from src.perception.tracker import CatTracker

        tracker = CatTracker(frame_size=(640, 480))
        target = tracker.update([
            {"bbox": (0, 0, 100, 100), "conf": 0.8, "cx": 50, "cy": 50, "w": 100, "h": 100},
            {"bbox": (0, 0, 200, 200), "conf": 0.7, "cx": 100, "cy": 100, "w": 200, "h": 200},
        ])

        self.assertEqual(target["bbox"], (0, 0, 200, 200))
        self.assertEqual(target["missing_count"], 0)

    def test_retains_nearby_prior_target_over_larger_distant_detection(self):
        from src.perception.tracker import CatTracker

        tracker = CatTracker(frame_size=(640, 480), retention_distance=80)
        tracker.update([
            {"bbox": (90, 90, 190, 190), "conf": 0.8, "cx": 140, "cy": 140, "w": 100, "h": 100},
        ])
        target = tracker.update([
            {"bbox": (95, 95, 195, 195), "conf": 0.8, "cx": 145, "cy": 145, "w": 100, "h": 100},
            {"bbox": (400, 300, 620, 470), "conf": 0.9, "cx": 510, "cy": 385, "w": 220, "h": 170},
        ])

        self.assertEqual(target["cx"], 145)

    def test_empty_detections_increments_missing_count(self):
        from src.perception.tracker import CatTracker

        tracker = CatTracker(frame_size=(640, 480))
        tracker.update([
            {"bbox": (0, 0, 100, 100), "conf": 0.8, "cx": 50, "cy": 50, "w": 100, "h": 100},
        ])
        target = tracker.update([])

        self.assertEqual(target["missing_count"], 1)
        self.assertTrue(target["missing"])

    def test_target_includes_normalized_metrics(self):
        from src.perception.tracker import CatTracker

        tracker = CatTracker(frame_size=(640, 480))
        target = tracker.update([
            {"bbox": (270, 190, 370, 290), "conf": 0.8, "cx": 320, "cy": 240, "w": 100, "h": 100},
        ])

        self.assertAlmostEqual(target["center_offset_x"], 0.0)
        self.assertAlmostEqual(target["size_ratio"], 10000 / (640 * 480))


if __name__ == "__main__":
    unittest.main()
