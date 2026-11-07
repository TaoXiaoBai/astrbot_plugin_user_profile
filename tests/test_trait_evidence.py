import copy
import unittest

from trait_evidence import merge_scene_long_term_traits, review_long_term_traits


class TraitEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.materials = {
            "r_one": {"t": 86401, "scene": "group", "group_id": "123"},
            "r_same_day": {"t": 86402, "scene": "group", "group_id": "123"},
            "r_two": {"t": 172801, "scene": "private", "group_id": ""},
            "t_summary": {"t": 172802, "scene": "private"},
        }

    def trait(self, refs=None, **updates):
        item = {"trait": "Patient", "confidence": 0.7, "reason": "Listens carefully"}
        if refs is not None:
            item["evidence_refs"] = refs
        item.update(updates)
        return item

    def review(self, previous=(), current=(), retired=(), now=200000):
        return review_long_term_traits(previous, current, retired, self.materials, now)

    def test_new_traits_require_real_timestamped_material(self):
        for refs in (None, [], ["r_forged"], ["t_summary"], ["other"], "r_one"):
            with self.subTest(refs=refs):
                self.assertEqual(self.review(current=[self.trait(refs)]), [])
        for timestamp in (0, -1, float("nan"), float("inf"), -float("inf"), "86401", True, None):
            with self.subTest(timestamp=timestamp):
                self.materials["r_bad"] = {"t": timestamp, "scene": "group"}
                self.assertEqual(self.review(current=[self.trait(["r_bad"])]), [])
        result = self.review(current=[self.trait(["r_one", "r_forged", "t_summary"])])[0]
        self.assertEqual(result["evidence_refs"], ["r_one"])
        self.assertEqual(result["support_count"], 1)

    def test_single_confirmation_is_tentative(self):
        result = self.review(current=[self.trait(["r_one", "r_one"])])[0]
        self.assertEqual(result["stability"], "tentative")
        self.assertEqual(result["support_count"], 1)
        self.assertEqual(result["evidence_days"], [1])
        self.assertEqual(result["scenes"], ["group"])
        self.assertEqual(result["first_seen"], 200000)
        self.assertEqual(result["last_confirmed"], 200000)
        self.assertEqual(result["first_evidence_at"], 86401)
        self.assertEqual(result["last_evidence_at"], 86401)

    def test_cross_day_confirmation_and_repeated_ref(self):
        old = self.review(current=[self.trait(["r_one"])])
        same_day = self.review(old, [self.trait(["r_same_day"])], now=210000)
        self.assertEqual(same_day[0]["stability"], "tentative")
        result = self.review(same_day, [self.trait(["r_two"], trait=" patient ", confidence=0.9, reason="More evidence")], now=220000)
        item = result[0]
        self.assertEqual(item["trait"], "Patient")
        self.assertEqual(item["confidence"], 0.9)
        self.assertEqual(item["reason"], "More evidence")
        self.assertEqual(item["support_count"], 3)
        self.assertEqual(item["evidence_days"], [1, 2])
        self.assertEqual(item["stability"], "established")
        self.assertEqual(item["scenes"], ["group", "private"])
        self.assertEqual(item["first_seen"], 200000)
        self.assertEqual(item["first_evidence_at"], 86401)
        self.assertEqual(item["last_evidence_at"], 172801)
        repeated = self.review(result, [self.trait(["r_two", "r_one"])], now=230000)[0]
        self.assertEqual(repeated["support_count"], 3)
        self.assertEqual(repeated["last_confirmed"], 230000)

    def test_omitted_or_ungrounded_traits_preserve_old_record(self):
        old = self.review(current=[self.trait(["r_one"])])
        self.assertEqual(self.review(old, [], now=250000), old)
        for refs in (None, ["t_summary"], ["r_forged"]):
            with self.subTest(refs=refs):
                result = self.review(old, [self.trait(refs, confidence=1, reason="Unsupported")], now=250000)
                self.assertEqual(result, old)

    def test_retirement_requires_matching_trait_and_current_evidence(self):
        old = self.review(current=[self.trait(["r_one"])])
        for item in (self.trait(), self.trait(["t_summary"]), self.trait(["r_forged"]), self.trait(["r_two"], trait="Other")):
            self.assertEqual(self.review(old, retired=[item]), old)
        self.assertEqual(self.review(old, retired=[self.trait(["r_two"], trait=" PATIENT ")]), [])
        self.materials.pop("r_one")
        self.assertEqual(self.review(old, retired=[self.trait(["r_one"])]), old)

    def test_legacy_stays_tentative_and_resets_unverifiable_count(self):
        legacy = [self.trait(first_seen=100, last_confirmed=200, support_count=7, stability="established")]
        result = self.review(legacy)[0]
        self.assertEqual(result["support_count"], 0)
        self.assertEqual(result["stability"], "tentative")
        self.assertEqual(result["last_confirmed"], 200)
        result = self.review(legacy, [self.trait(["r_one"])])[0]
        self.assertEqual(result["support_count"], 1)
        self.assertEqual(result["first_seen"], 100)
        self.assertEqual(result["stability"], "tentative")

    def test_bounds_order_and_empty_traits(self):
        materials = {"r_" + str(i): {"t": (i + 1) * 86400, "scene": "group"} for i in range(40)}
        current = [self.trait(list(materials))]
        result = review_long_term_traits([], current, [], materials, 4000000)
        item = result[0]
        self.assertEqual(len(item["evidence_refs"]), 16)
        self.assertEqual(len(item["evidence_days"]), 30)
        self.assertEqual(item["support_count"], 16)
        self.assertEqual(item["first_evidence_at"], 86400)
        self.assertEqual(item["last_evidence_at"], 40 * 86400)
        result = self.review(result, [self.trait(["r_two"], trait="New")])
        self.assertEqual([item["trait"] for item in result], ["Patient", "New"])
        items = [self.trait(["r_one"], trait=str(i)) for i in range(8)]
        items.insert(0, self.trait(["r_one"], trait="  "))
        result = self.review(current=items)
        self.assertEqual([item["trait"] for item in result], [str(i) for i in range(5)])

    def test_input_and_output_are_independent(self):
        previous = [self.trait(["r_one"], first_seen=100, last_confirmed=200,
                               evidence_days=[1], support_count=1, scenes=["group"], extra={"nested": [1]})]
        current = [self.trait(["r_two"])]
        retired = [self.trait(["r_forged"])]
        inputs = (previous, current, retired, self.materials)
        snapshot = copy.deepcopy(inputs)
        result = self.review(previous, current, retired)
        self.assertEqual(inputs, snapshot)
        result[0]["extra"]["nested"].append(2)
        result[0]["evidence_refs"].append("r_output_only")
        self.assertEqual(inputs, snapshot)

    def test_scene_merge_unions_evidence_without_new_confirmation(self):
        group = self.review(current=[self.trait(["r_one"])], now=200000)
        private = self.review(current=[self.trait(["r_two"], trait=" patient ", confidence=0.9, reason="Private evidence")], now=300000)
        snapshot = copy.deepcopy((group, private))
        result = merge_scene_long_term_traits(group, private)
        self.assertEqual((group, private), snapshot)
        item = result[0]
        self.assertEqual(item["trait"], "Patient")
        self.assertEqual(item["evidence_refs"], ["r_one", "r_two"])
        self.assertEqual(item["evidence_days"], [1, 2])
        self.assertEqual(item["scenes"], ["group", "private"])
        self.assertEqual(item["support_count"], 2)
        self.assertEqual(item["confidence"], 0.9)
        self.assertEqual(item["reason"], "Private evidence")
        self.assertEqual(item["first_seen"], 200000)
        self.assertEqual(item["last_confirmed"], 300000)
        self.assertEqual(item["first_evidence_at"], 86401)
        self.assertEqual(item["last_evidence_at"], 172801)
        self.assertEqual(item["stability"], "established")
        self.assertEqual(merge_scene_long_term_traits(result, group), result)
        item["evidence_refs"].append("r_changed")
        self.assertEqual((group, private), snapshot)

    def test_scene_merge_legacy_count_timestamps_and_order(self):
        group = [self.trait(first_seen=0, last_confirmed=100, support_count=9)]
        private = [self.trait(first_seen=50, last_confirmed=90, support_count=3, confidence=0.5, reason="Lower")]
        result = merge_scene_long_term_traits(group, private)[0]
        self.assertEqual(result["support_count"], 0)
        self.assertEqual(result["first_seen"], 50)
        self.assertEqual(result["last_confirmed"], 100)
        self.assertEqual(result["confidence"], 0.7)
        self.assertEqual(result["reason"], "Listens carefully")
        self.assertEqual(result["stability"], "tentative")
        no_confirmation = merge_scene_long_term_traits([self.trait()], [self.trait()])[0]
        self.assertNotIn("last_confirmed", no_confirmation)
        items = [self.trait(trait=str(i)) for i in range(8)]
        result = merge_scene_long_term_traits(items[:2], items[2:] + [self.trait(trait=" ")])
        self.assertEqual([item["trait"] for item in result], [str(i) for i in range(5)])

    def test_scene_merge_ref_deduplication_and_legacy_counts(self):
        group = self.review(current=[self.trait(["r_one"])])
        private = copy.deepcopy(group)
        private[0]["support_count"] = 99
        result = merge_scene_long_term_traits(group, private)[0]
        self.assertEqual(result["support_count"], 1)
        self.assertEqual(result["stability"], "tentative")
        legacy = [self.trait(support_count=100)]
        self.assertEqual(merge_scene_long_term_traits(legacy, group)[0]["support_count"], 1)

    def test_resubmitting_evicted_reference_keeps_bounded_support_count(self):
        materials = {"r_" + str(i): {"t": (i + 1) * 86400, "scene": "group"} for i in range(17)}
        result = review_long_term_traits([], [self.trait(list(materials))], [], materials, 2000000)
        self.assertNotIn("r_0", result[0]["evidence_refs"])
        self.assertEqual(result[0]["support_count"], 16)
        result = review_long_term_traits(result, [self.trait(["r_0"])], [], materials, 2100000)
        self.assertIn("r_0", result[0]["evidence_refs"])
        self.assertEqual(len(result[0]["evidence_refs"]), 16)
        self.assertEqual(result[0]["support_count"], 16)
        repeated = review_long_term_traits(result, [self.trait(["r_0"])], [], materials, 2200000)
        self.assertEqual(repeated[0]["support_count"], 16)

    def test_review_normalizes_counts_without_current_confirmation(self):
        for refs, expected in ((None, 0), ([], 0), (["t_summary"], 0),
                               (["r_one", "r_one", "t_summary", "r_two"], 2),
                               (["r_" + str(i) for i in range(20)], 16)):
            for count in (0, 99):
                with self.subTest(refs=refs, count=count):
                    previous = [self.trait(refs, support_count=count, last_confirmed=100)]
                    result = self.review(previous)[0]
                    self.assertEqual(result["support_count"], expected)
                    self.assertEqual(result["support_count"], len(result["evidence_refs"]))
                    self.assertEqual(result["last_confirmed"], 100)

    def test_scene_merge_counts_only_retained_unique_refs(self):
        group = [self.trait(["r_" + str(i) for i in range(20)], support_count=99,
                            evidence_days=list(range(40)), last_confirmed=100)]
        private = [self.trait(["r_" + str(i) for i in range(12, 28)], support_count=0,
                              evidence_days=list(range(20, 50)), last_confirmed=200)]
        for left, right in ((group, []), ([], group), (group, private)):
            with self.subTest(left=left, right=right):
                result = merge_scene_long_term_traits(left, right)[0]
                self.assertEqual(result["support_count"], 16)
                self.assertEqual(result["support_count"], len(result["evidence_refs"]))
                self.assertEqual(len(result["evidence_days"]), 30)
                self.assertEqual(result["stability"], "established")
        merged = merge_scene_long_term_traits(group, private)[0]
        self.assertEqual(merged["evidence_refs"], private[0]["evidence_refs"])
        self.assertEqual(merged["last_confirmed"], 200)
        self.assertEqual(merge_scene_long_term_traits([self.trait(support_count=99)], [])[0]["support_count"], 0)


if __name__ == "__main__":
    unittest.main()
