"""Unit tests for pure functions in utils_models.py."""
import pytest
import pathlib
import sys

TESTS_DIR = pathlib.Path(__file__).resolve().parents[1]
PLUGIN_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))


class TestDetermineDefaultModel:
    """Tests for ModelConfigurationService.determine_default_model - extracted as pure function."""

    @staticmethod
    def determine_default_model(default_model_name_secret, default_model_project_id_secret, distinct_items: dict):
        """Pure function extracted from ModelConfigurationService."""
        available_models = [(proj_id, model_name) for (proj_id, model_name) in distinct_items.keys()]
        default_model_name = None
        default_model_project_id = None

        if default_model_name_secret and default_model_project_id_secret:
            for (proj_id, model_name) in available_models:
                if model_name == default_model_name_secret and str(proj_id) == str(default_model_project_id_secret):
                    default_model_name = model_name
                    default_model_project_id = proj_id
                    break

        if default_model_name is None and available_models:
            default_model_name = available_models[0][1]
            default_model_project_id = available_models[0][0]

        return default_model_name, default_model_project_id

    def test_finds_exact_match(self):
        distinct_items = {
            (1, "gpt-5.4"): {"name": "gpt-5.4", "shared": False},
            (1, "gpt-5-mini"): {"name": "gpt-5-mini", "shared": False},
            (2, "gpt-5.4"): {"name": "gpt-5.4", "shared": True},
        }
        name, proj_id = self.determine_default_model("gpt-5.4", "2", distinct_items)
        assert name == "gpt-5.4"
        assert proj_id == 2

    def test_fallback_to_first_when_no_match(self):
        distinct_items = {
            (1, "gpt-5.4"): {"name": "gpt-5.4", "shared": False},
            (1, "gpt-5-mini"): {"name": "gpt-5-mini", "shared": False},
        }
        name, proj_id = self.determine_default_model("nonexistent", "99", distinct_items)
        # Falls back to first available
        assert name == "gpt-5.4"
        assert proj_id == 1

    def test_fallback_when_no_secrets(self):
        distinct_items = {
            (1, "model-a"): {"name": "model-a", "shared": False},
        }
        name, proj_id = self.determine_default_model(None, None, distinct_items)
        assert name == "model-a"
        assert proj_id == 1

    def test_empty_items_returns_none(self):
        name, proj_id = self.determine_default_model("gpt-5.4", "1", {})
        assert name is None
        assert proj_id is None


class TestDetermineExplicitDefaultModel:
    """Tests for ModelConfigurationService.determine_explicit_default_model."""

    @staticmethod
    def determine_explicit_default_model(default_model_name_secret, default_model_project_id_secret, distinct_items: dict, predicate=None):
        """Pure function extracted from ModelConfigurationService."""
        if not default_model_name_secret or not default_model_project_id_secret:
            return None, None

        for (proj_id, model_name), model_data in distinct_items.items():
            if model_name != default_model_name_secret:
                continue
            if str(proj_id) != str(default_model_project_id_secret):
                continue
            if predicate and not predicate(model_data):
                return None, None
            return model_name, proj_id

        return None, None

    def test_finds_match_without_predicate(self):
        distinct_items = {
            (1, "gpt-5.4"): {"name": "gpt-5.4", "low_tier": True},
        }
        name, proj_id = self.determine_explicit_default_model("gpt-5.4", "1", distinct_items)
        assert name == "gpt-5.4"
        assert proj_id == 1

    def test_respects_predicate(self):
        distinct_items = {
            (1, "gpt-5.4"): {"name": "gpt-5.4", "low_tier": False},
        }
        name, proj_id = self.determine_explicit_default_model(
            "gpt-5.4", "1", distinct_items,
            predicate=lambda m: m.get("low_tier") is True
        )
        assert name is None
        assert proj_id is None

    def test_predicate_passes(self):
        distinct_items = {
            (1, "gpt-5.4"): {"name": "gpt-5.4", "low_tier": True},
        }
        name, proj_id = self.determine_explicit_default_model(
            "gpt-5.4", "1", distinct_items,
            predicate=lambda m: m.get("low_tier") is True
        )
        assert name == "gpt-5.4"
        assert proj_id == 1

    def test_no_secrets_returns_none(self):
        distinct_items = {
            (1, "gpt-5.4"): {"name": "gpt-5.4"},
        }
        name, proj_id = self.determine_explicit_default_model(None, None, distinct_items)
        assert name is None
        assert proj_id is None

    def test_no_match_returns_none(self):
        distinct_items = {
            (1, "gpt-5.4"): {"name": "gpt-5.4"},
        }
        name, proj_id = self.determine_explicit_default_model("gpt-5", "1", distinct_items)
        assert name is None
        assert proj_id is None


class TestPrepareResponse:
    """Tests for ModelConfigurationService.prepare_response."""

    @staticmethod
    def prepare_response(distinct_items: dict, default_model_name: str, default_model_project_id: int):
        """Pure function extracted from ModelConfigurationService."""
        items_with_default_flag = []
        for (proj_id, model_name), model_data in distinct_items.items():
            model_data['default'] = (
                model_name == default_model_name and proj_id == default_model_project_id
            )
            items_with_default_flag.append(model_data)

        if items_with_default_flag:
            items_with_default_flag.sort(key=lambda x: (
                not x['shared'],
                (x.get('display_name') or x.get('name', '')).lower()
            ))

        return {
            "total": len(distinct_items),
            "items": items_with_default_flag,
            "default_model_name": default_model_name,
            "default_model_project_id": default_model_project_id,
        }

    def test_marks_default_correctly(self):
        distinct_items = {
            (1, "gpt-5.4"): {"name": "gpt-5.4", "shared": False},
            (2, "gpt-5-mini"): {"name": "gpt-5-mini", "shared": True},
        }
        result = self.prepare_response(distinct_items, "gpt-5.4", 1)

        assert result["total"] == 2
        assert result["default_model_name"] == "gpt-5.4"
        assert result["default_model_project_id"] == 1

        gpt4 = next(i for i in result["items"] if i["name"] == "gpt-5.4")
        assert gpt4["default"] is True

        gpt35 = next(i for i in result["items"] if i["name"] == "gpt-5-mini")
        assert gpt35["default"] is False

    def test_sorts_shared_first(self):
        distinct_items = {
            (1, "z-private"): {"name": "z-private", "shared": False},
            (2, "a-shared"): {"name": "a-shared", "shared": True},
        }
        result = self.prepare_response(distinct_items, None, None)

        # Shared should come first
        assert result["items"][0]["shared"] is True
        assert result["items"][0]["name"] == "a-shared"

    def test_sorts_alphabetically_within_shared_status(self):
        distinct_items = {
            (1, "zebra"): {"name": "zebra", "shared": False},
            (1, "alpha"): {"name": "alpha", "shared": False},
            (1, "beta"): {"name": "beta", "shared": False},
        }
        result = self.prepare_response(distinct_items, None, None)

        names = [i["name"] for i in result["items"]]
        assert names == ["alpha", "beta", "zebra"]

    def test_empty_items(self):
        result = self.prepare_response({}, None, None)

        assert result["total"] == 0
        assert result["items"] == []
        assert result["default_model_name"] is None
