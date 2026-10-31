import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from artifex_bridge.client import ArtifexClient, ArtifexError, ArtifexTaggingError, _decorate, _matches


def term(value, scope="tag", **kwargs):
    return dict(value=value, scope=scope, operator="and", negated=False, open=0, close=0) | kwargs


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = ArtifexClient(SimpleNamespace())

    def test_all_duplicate_names_are_disambiguated(self):
        rows = _decorate([{"id": 1, "name": "海边"}, {"id": 2, "name": "海边"}, {"id": 3, "name": "雪山"}])
        self.assertEqual([r["label"] for r in rows], ["海边(id:1)", "海边(id:2)", "雪山"])

    def test_named_group_uses_highest_count_and_stable_tie(self):
        self.client.groups = Mock(return_value=[{"id": 1, "name": "sea", "illustration_count": 2}, {"id": 3, "name": "sea", "illustration_count": 8}, {"id": 2, "name": "sea", "illustration_count": 8}])
        self.assertEqual(self.client.resolve_group(name="sea")["id"], 2)

    def test_invalid_input_falls_back_to_selected_group(self):
        self.client.groups = Mock(return_value=[{"id": 4, "name": "saved", "value": "id:4", "label": "saved", "illustration_count": 0}])
        self.assertEqual(self.client.resolve_group(name="图" * 34, selected="id:4")["id"], 4)

    def test_missing_input_name_is_created(self):
        self.client.groups = Mock(return_value=[])
        self.client._request = Mock(return_value={"id": 3, "name": "new"})
        self.assertEqual(self.client.resolve_group(name="new")["id"], 3)
        self.client._request.assert_called_once_with("POST", "/api/groups", payload={"name": "new"})

    def test_color_counts_include_computed_rules_and_manual_override(self):
        colors = {"sets": [{"id": "board", "pairs": [
            {"id": "a", "terms": [term("sea")]}, {"id": "b", "terms": [term("blue")]},
        ], "match_order": ["a", "b"], "manual_assignments": {"3": "b"}}]}
        self.client.illustrations = Mock(return_value=[{"id": 1, "tags": "sea,blue"}, {"id": 2, "tags": "blue"}, {"id": 3, "tags": "sea"}])
        self.assertEqual(self.client._color_counts(1, colors), {("board", "a"): 1, ("board", "b"): 2})

    def test_mixed_rules_preserve_parentheses_negation_and_precedence(self):
        # (sea OR sky) AND NOT night
        pair = {"terms": [term("sea", open=1), term("sky", operator="or", close=1), term("night", negated=True)]}
        self.assertTrue(_matches({"tags": "sky"}, pair))
        self.assertFalse(_matches({"tags": "sea,night"}, pair))
        self.assertFalse(_matches({"tags": "seaside"}, pair))
        self.assertTrue(_matches({"extended_data": {"Positive Prompt": "deep BLUE sea"}}, {"terms": [term("blue", "prompt")]}))

    def test_only_permanent_content_errors_are_skipped(self):
        self.client._request = Mock(return_value={"failed": [{"error": "cannot identify image: x.png"}]})
        self.assertEqual(self.client.upload(1, "x.png", b"x").status, "skipped")
        self.client._request.return_value = {"failed": [{"error": "failed to commit x.png: disk full"}]}
        with self.assertRaisesRegex(ArtifexError, "disk full"):
            self.client.upload(1, "x.png", b"x")

    def test_upload_without_tagging_skips_auto_tag_and_preserves_bytes(self):
        self.client._request = Mock(return_value={"added": [{"id": 7}]})
        result = self.client.upload(1, "20261031_1.png", b"\x00png", auto_tag=False)
        self.assertEqual(result.illustration["id"], 7)
        body = self.client._request.call_args.kwargs["body"]
        self.assertIn(b'name="skip_auto_tag"\r\n\r\ntrue', body)
        self.assertIn(b"\x00png", body)
        self.client._request.assert_called_once()

    def test_enabled_tagging_uses_explicit_retag_for_only_the_saved_image(self):
        self.client._request = Mock(side_effect=[{"auto_tag": False}, {"auto_tag": True}, {"added": [{"id": 7}]}, {"updated": [{"id": 7, "tags": "blue, sky"}]}])
        result = self.client.upload(1, "20261031_1.png", b"\x00png", auto_tag=True, library_id="library")
        self.assertEqual(result.illustration["tags"], "blue, sky")
        calls = self.client._request.call_args_list
        self.assertEqual(calls[0].args, ("GET", "/api/settings"))
        self.assertEqual(calls[1].args, ("PUT", "/api/settings"))
        self.assertEqual(calls[1].kwargs, {"payload": {"auto_tag": True}, "library_id": "library"})
        self.assertIn(b'name="skip_auto_tag"\r\n\r\ntrue', calls[2].kwargs["body"])
        self.assertEqual(calls[3].args, ("POST", "/api/illustrations/retag"))
        self.assertEqual(calls[3].kwargs, {"payload": {"ids": [7]}, "library_id": "library"})
        self.assertEqual(len(calls), 4)

    def test_enabled_engine_is_not_reloaded_for_every_upload(self):
        self.client._request = Mock(side_effect=[{"auto_tag": True}, {"added": [{"id": 7}]}, {"updated": [{"id": 7, "tags": "blue"}]}])
        result = self.client.upload(1, "20261031_1.png", b"\x00png", auto_tag=True)
        self.assertEqual(result.illustration["tags"], "blue")
        self.assertEqual([call.args[0] for call in self.client._request.call_args_list], ["GET", "POST", "POST"])

    def test_tagging_failure_reports_that_image_was_already_saved(self):
        self.client._request = Mock(side_effect=[{"auto_tag": True}, {"added": [{"id": 7}]}, ArtifexError("Tagger model is not loaded", 503)])
        with self.assertRaisesRegex(ArtifexTaggingError, "Image 7 was saved.*model is not loaded") as caught:
            self.client.upload(1, "20261031_1.png", b"\x00png", auto_tag=True)
        self.assertEqual(caught.exception.illustration["id"], 7)
        self.assertEqual(caught.exception.status, 503)

    def test_per_image_tagging_error_is_not_reported_as_success(self):
        self.client._request = Mock(side_effect=[{"auto_tag": True}, {"added": [{"id": 7}]}, {"updated": [], "failed": [{"error": "cannot decode image"}]}])
        with self.assertRaisesRegex(ArtifexTaggingError, "cannot decode image"):
            self.client.upload(1, "20261031_1.png", b"\x00png", auto_tag=True)

    def test_library_session_keeps_old_library_header_after_user_switch(self):
        process = SimpleNamespace(
            library_id="first", config=SimpleNamespace(request_timeout=2),
            ensure_running=Mock(return_value="http://127.0.0.1:1234"),
            expected_library=lambda token: token,
        )
        client = ArtifexClient(process)
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"[]"
        client._opener.open = Mock(return_value=response)
        with client.library_session() as token:
            self.assertEqual(token, "first")
            process.library_id = "second"
            client._request("GET", "/api/groups")
            request = client._opener.open.call_args.args[0]
            self.assertEqual(request.get_header("X-artifex-library"), "first")
        client._request("GET", "/api/groups")
        request = client._opener.open.call_args.args[0]
        self.assertEqual(request.get_header("X-artifex-library"), "second")


if __name__ == "__main__":
    unittest.main()
