"""Focused tests for text-classification output parsing guidance in the generation request.

The transformers text-classification pipeline can return ``list[dict]`` or ``list[list[dict]]``. The instructions given to
OpenCode include a reference normaliser (``gradio_gen._TEXT_CLASSIFICATION_HELPER``). These tests run that exact source for both
shapes and check that the request carries it. They do not start OpenCode or load any model.
"""
import ast
import unittest

from m2g import gradio_gen as gg
from test_analyzer import analyze_preset

FLAT = [{"label": "NEGATIVE", "score": 0.1}, {"label": "POSITIVE", "score": 0.9}]
NESTED = [[{"label": "NEGATIVE", "score": 0.1}, {"label": "POSITIVE", "score": 0.9}]]


def helper():
    ns = {}
    exec(gg._TEXT_CLASSIFICATION_HELPER, ns)  # the exact text shown to OpenCode
    return ns["_normalize_classification"]


def top_label(results):
    flat = helper()(results)
    best = sorted(flat, key=lambda d: d["score"], reverse=True)[0]
    return best["label"], best["score"]


class TestClassificationNormaliser(unittest.TestCase):
    def test_flat_list_of_dicts(self):
        self.assertEqual(helper()(FLAT), FLAT)
        self.assertEqual(top_label(FLAT), ("POSITIVE", 0.9))

    def test_nested_list_of_dicts(self):
        self.assertEqual(helper()(NESTED), FLAT)
        self.assertEqual(top_label(NESTED), ("POSITIVE", 0.9))

    def test_both_shapes_give_the_same_result(self):
        self.assertEqual(helper()(FLAT), helper()(NESTED))
        self.assertEqual(top_label(FLAT), top_label(NESTED))

    def test_single_dict_and_empty_or_malformed_input(self):
        fn = helper()
        self.assertEqual(fn({"label": "A", "score": 1.0}), [{"label": "A", "score": 1.0}])
        self.assertEqual(fn([]), [])
        self.assertEqual(fn(None), [])
        self.assertEqual(fn([[], "junk", 3, [{"label": "A", "score": 1.0}]]), [{"label": "A", "score": 1.0}])

    def test_helper_is_valid_module_level_def_only(self):
        body = ast.parse(gg._TEXT_CLASSIFICATION_HELPER).body
        self.assertEqual([type(n).__name__ for n in body], ["FunctionDef"])  # the generated app allows only defs at module level


class TestRequestCarriesTheGuidance(unittest.TestCase):
    def test_request_contains_helper_and_both_shapes(self):
        request = gg.build_request(analyze_preset("sst2"))
        self.assertIn("_normalize_classification", request)
        self.assertIn("flat list of dicts", request)
        self.assertIn("nested list of dicts", request)
        self.assertIn("Never assume", request)
        self.assertIn("results[0]", request)
        for line in gg._TEXT_CLASSIFICATION_HELPER.splitlines():
            self.assertIn(line, request)

    def test_existing_request_structure_is_preserved(self):
        request = gg.build_request(analyze_preset("sst2"))
        for marker in ("## Section 1", "## Section 2", "## Output format", gg.DATA_BEGIN, gg.DATA_END):
            self.assertIn(marker, request)
        self.assertEqual(request.count(gg.DATA_BEGIN), 1)  # the new text must not add or fake a data marker


if __name__ == "__main__":
    unittest.main()
