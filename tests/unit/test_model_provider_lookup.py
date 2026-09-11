"""configurations_get_model_provider — the credential family behind a model name.

The value feeds usage metering, where it narrows which wire dialect parses the response body.
A wrong family mislabels a call; a raised exception would break provider narrowing for every
LLM call at the gateway. So the assertions here are about the two ways this can go wrong:
raising on data that legitimately exists, and answering from a row it must not read.

The module talks to Postgres through `db.get_session`, so it is loaded against a tiny in-memory
stand-in for the ORM instead of having its logic copied here — that way the real filters,
the real query count and the real agreement rule are what gets exercised.

Row shapes are taken from live `p_*.configuration` data:
  - `p_1` section `llm` has three rows named `gpt-5-mini-2025-08-07`, all pointing at one
    credential — a duplicate that `.scalar()` used to turn into MultipleResultsFound.
  - `p_1` has two rows named `eu.anthropic.claude-3-7-sonnet-20250219-v1:0` pointing at
    credentials of *different* types — a genuine disagreement.
  - every `p_1.ai_credentials` row is `shared=false`, which is why the shared filter must
    apply to the model row only.
"""
import pathlib
import sys
import types

import pytest

from fixtures.helpers import load_module_with_stubs


PUBLIC_PROJECT_ID = 1
CALLER_PROJECT_ID = 3


class Column:
    """A stand-in for a mapped column that records comparisons instead of building SQL."""

    def __init__(self, name):
        self.name = name

    def __getitem__(self, key):
        return Column(f"{self.name}[{key}]")

    @property
    def astext(self):
        return Column(f"{self.name}.astext")

    def __eq__(self, other):
        return ("eq", self.name, other)

    def is_(self, other):
        return ("eq", self.name, other)

    def __hash__(self):
        return hash(self.name)


class Configuration:  # pylint: disable=R0903
    project_id = Column("project_id")
    section = Column("section")
    shared = Column("shared")
    elitea_title = Column("elitea_title")
    type = Column("type")
    data = Column("data")


def read(row, name):
    """The value a recorded comparison refers to, for one table row."""
    if name == "data[name].astext":
        return row["data"].get("name")
    #
    if name == "data[ai_credentials]":
        return row["data"].get("ai_credentials")
    #
    return row.get(name)


class Session:
    """Filters the flat table in Python; the project scoping must come from the filters."""

    def __init__(self, table, log):
        self.table = table
        self.log = log
        self.entity = None
        self.filters = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def query(self, entity):
        self.entity = entity
        self.filters = []
        return self

    def filter(self, *conditions):
        self.filters.extend(conditions)
        return self

    def _rows(self):
        self.log.append((self.entity.name, list(self.filters)))
        #
        return [
            row for row in self.table
            if all(read(row, name) == value for _, name, value in self.filters)
        ]

    def all(self):
        return [(read(row, self.entity.name),) for row in self._rows()]

    def first(self):
        rows = self.all()
        #
        return rows[0] if rows else None


class DB:
    """`db` as the module sees it: one flat table shared by every project."""

    def __init__(self, table):
        self.table = table
        self.queries = []

    def get_session(self, project_id=None):  # pylint: disable=W0613
        return Session(self.table, self.queries)


def model_row(name, credential, project_id=PUBLIC_PROJECT_ID, section="llm", shared=True):
    return {
        "project_id": project_id, "section": section, "shared": shared,
        "elitea_title": f"{name}-{credential}", "type": "llm_model",
        "data": {"name": name, "ai_credentials": credential},
    }


def credential_row(title, credential_type, project_id=PUBLIC_PROJECT_ID):
    """Credentials are never shared — the flag that matters lives on the model row."""
    return {
        "project_id": project_id, "section": "ai_credentials", "shared": False,
        "elitea_title": title, "type": credential_type, "data": {},
    }


def points_at(title, private=False):
    return {"elitea_title": title, "private": private}


@pytest.fixture(scope="module")
def getters():
    """rpc/getters.py loaded with its relative imports satisfied by stubs."""
    plugin_root = pathlib.Path(__file__).resolve().parent.parent.parent
    package = "cfg_under_test"
    #
    def stub(name, **attributes):
        module = types.ModuleType(name)
        #
        for key, value in attributes.items():
            setattr(module, key, value)
        #
        return module

    root = stub(package)
    root.__path__ = [str(plugin_root)]
    rpc_package = stub(f"{package}.rpc")
    rpc_package.__path__ = [str(plugin_root / "rpc")]
    models = stub(f"{package}.models")
    models.__path__ = []
    pd_models = stub(f"{package}.models.pd")
    pd_models.__path__ = []
    #
    stubs = {
        package: root,
        f"{package}.rpc": rpc_package,
        f"{package}.common_utils": stub(
            f"{package}.common_utils", get_public_project_id=lambda: PUBLIC_PROJECT_ID,
        ),
        f"{package}.local_tools": stub(
            f"{package}.local_tools", db=None, log=sys.modules["pylon.core.tools"].log,
        ),
        f"{package}.models": models,
        f"{package}.models.configuration": stub(
            f"{package}.models.configuration", Configuration=Configuration,
        ),
        f"{package}.models.pd": pd_models,
        f"{package}.models.pd.llm_model": stub(
            f"{package}.models.pd.llm_model", LlmModelList=object,
        ),
        f"{package}.utils": stub(
            f"{package}.utils",
            get_configuration_llm_models_with_limits_query=lambda *a, **k: None,
            get_configurations=lambda *a, **k: None,
        ),
        f"{package}.utils_getters": stub(
            f"{package}.utils_getters",
            get_user_configurations=lambda *a, **k: None,
            get_all_project_configurations=lambda *a, **k: None,
            get_project_configurations=lambda *a, **k: None,
            get_project_configuration=lambda *a, **k: None,
        ),
        f"{package}.utils_models": stub(f"{package}.utils_models", ModelConfigurationService=object),
    }
    #
    return load_module_with_stubs(
        plugin_root / "rpc" / "getters.py", f"{package}.rpc.getters", stubs,
    )


@pytest.fixture()
def ask(getters, monkeypatch):
    """Answer configurations_get_model_provider against a given table."""
    resource = getters.RPC()

    def query(table, project_id=CALLER_PROJECT_ID, public_project_id=PUBLIC_PROJECT_ID, **kwargs):
        database = DB(table)
        monkeypatch.setattr(getters, "db", database)
        monkeypatch.setattr(getters, "get_public_project_id", lambda: public_project_id)
        #
        answer = resource.configurations_get_model_provider(
            project_id=project_id, model_name=kwargs.pop("model_name", "gpt-4o"), **kwargs
        )
        #
        return answer, database.queries

    return query


class TestTheHappyPath:

    def test_the_family_is_the_type_of_the_credential_the_model_points_at(self, ask):
        table = [
            model_row("gpt-4o", points_at("develiteaai"), project_id=CALLER_PROJECT_ID),
            credential_row("develiteaai", "open_ai", project_id=CALLER_PROJECT_ID),
        ]
        #
        provider, _ = ask(table)
        #
        assert provider == "open_ai"

    def test_an_unshared_credential_still_resolves(self, ask):
        # Every ai_credentials row on the platform is shared=false, so a shared filter on the
        # credential lookup would return nothing for every model there is.
        table = [
            model_row("gpt-4o", points_at("ai_dial_m"), project_id=CALLER_PROJECT_ID),
            credential_row("ai_dial_m", "ai_dial", project_id=CALLER_PROJECT_ID),
        ]
        #
        assert ask(table)[0] == "ai_dial"

    def test_the_section_argument_selects_which_rows_answer(self, ask):
        table = [
            model_row(
                "text-embedding-3-small", points_at("develiteaai"),
                project_id=CALLER_PROJECT_ID, section="embeddings",
            ),
            credential_row("develiteaai", "open_ai", project_id=CALLER_PROJECT_ID),
        ]
        #
        assert ask(table, model_name="text-embedding-3-small")[0] is None
        assert ask(
            table, model_name="text-embedding-3-small", section="embeddings",
        )[0] == "open_ai"


class TestDuplicateModelNames:
    """The model name is not unique in a project, which is what `.scalar()` could not survive."""

    def test_duplicates_agreeing_on_a_family_answer_it(self, ask):
        # Three live p_1 rows named gpt-5-mini-2025-08-07, all pointing at ai_dial_m.
        table = [
            model_row("gpt-5-mini-2025-08-07", points_at("ai_dial_m")),
            model_row("gpt-5-mini-2025-08-07", points_at("ai_dial_m")),
            model_row("gpt-5-mini-2025-08-07", points_at("ai_dial_m")),
            credential_row("ai_dial_m", "ai_dial"),
        ]
        #
        provider, _ = ask(table, model_name="gpt-5-mini-2025-08-07")
        #
        assert provider == "ai_dial"

    def test_duplicates_disagreeing_read_as_unknown(self, ask):
        # The live anthropic pair: one bedrock credential, one open_ai one. Answering either
        # would be a coin flip, and metering reads None as "sniff the body instead".
        table = [
            model_row("claude-3-7-sonnet", points_at("tigr-bedrock-updated")),
            model_row("claude-3-7-sonnet", points_at("develiteaai")),
            credential_row("tigr-bedrock-updated", "amazon_bedrock"),
            credential_row("develiteaai", "open_ai"),
        ]
        #
        provider, _ = ask(table, model_name="claude-3-7-sonnet")
        #
        assert provider is None

    def test_duplicates_pointing_at_different_credentials_of_one_family_agree(self, ask):
        table = [
            model_row("gpt-4o", points_at("dial-a")),
            model_row("gpt-4o", points_at("dial-b")),
            credential_row("dial-a", "ai_dial"),
            credential_row("dial-b", "ai_dial"),
        ]
        #
        assert ask(table)[0] == "ai_dial"


class TestTheProjectItResolvesIn:

    def test_the_callers_own_project_wins_over_the_public_one(self, ask):
        table = [
            model_row("gpt-4o", points_at("own"), project_id=CALLER_PROJECT_ID),
            credential_row("own", "azure_open_ai", project_id=CALLER_PROJECT_ID),
            model_row("gpt-4o", points_at("public")),
            credential_row("public", "open_ai"),
        ]
        #
        assert ask(table)[0] == "azure_open_ai"

    def test_a_shared_public_model_is_reachable_from_another_project(self, ask):
        table = [
            model_row("gpt-4o", points_at("public"), shared=True),
            credential_row("public", "open_ai"),
        ]
        #
        assert ask(table)[0] == "open_ai"

    def test_an_unshared_public_model_is_not_borrowed(self, ask):
        # p_1 really does hold unshared llm rows; another project cannot resolve them, so
        # neither may their family be read from here.
        table = [
            model_row("gpt-4o", points_at("public"), shared=False),
            credential_row("public", "open_ai"),
        ]
        #
        assert ask(table)[0] is None

    def test_the_public_project_is_not_asked_twice_when_it_is_the_caller(self, ask):
        table = [model_row("gpt-4o", points_at("nowhere"))]
        #
        _, queries = ask(table, project_id=PUBLIC_PROJECT_ID)
        #
        assert [name for name, _ in queries].count("data[ai_credentials]") == 1

    def test_the_callers_own_project_is_scoped_by_project_id(self, ask):
        table = [
            model_row("gpt-4o", points_at("elsewhere"), project_id=25),
            credential_row("elsewhere", "vertex_ai", project_id=25),
        ]
        #
        assert ask(table)[0] is None

    def test_no_public_project_configured_is_not_an_error(self, ask):
        table = [model_row("gpt-4o", points_at("public"))]
        #
        assert ask(table, public_project_id=None)[0] is None


class TestWhatItRefusesToGuess:

    def test_an_unknown_model_is_unknown(self, ask):
        assert ask([credential_row("develiteaai", "open_ai")])[0] is None

    def test_a_model_with_no_credential_reference_is_unknown(self, ask):
        table = [model_row("gpt-4o", None, project_id=CALLER_PROJECT_ID)]
        #
        assert ask(table)[0] is None

    def test_a_reference_without_a_title_is_unknown(self, ask):
        table = [model_row("gpt-4o", {"private": False}, project_id=CALLER_PROJECT_ID)]
        #
        assert ask(table)[0] is None

    def test_a_private_credential_lives_in_another_schema_so_it_is_unknown(self, ask):
        # `private` means the owner's personal project, which is not the project being queried;
        # a same-titled row here would be a different credential entirely.
        table = [
            model_row(
                "gpt-4o", points_at("develiteaai", private=True), project_id=CALLER_PROJECT_ID,
            ),
            credential_row("develiteaai", "open_ai", project_id=CALLER_PROJECT_ID),
        ]
        #
        assert ask(table)[0] is None

    def test_a_missing_credential_row_is_unknown(self, ask):
        table = [model_row("gpt-4o", points_at("deleted"), project_id=CALLER_PROJECT_ID)]
        #
        assert ask(table)[0] is None

    def test_only_a_row_in_the_credentials_section_can_supply_the_family(self, ask):
        # elitea_title is unique per schema but not per section, so without the section filter
        # this would answer `llm_model` — a family no dialect has ever heard of.
        table = [
            model_row("gpt-4o", points_at("collides"), project_id=CALLER_PROJECT_ID),
            dict(
                model_row("other", None, project_id=CALLER_PROJECT_ID),
                elitea_title="collides",
            ),
        ]
        #
        assert ask(table)[0] is None

    def test_one_unresolvable_duplicate_poisons_the_answer(self, ask):
        # Answering from the resolvable half would report a family for a model whose other
        # half may well belong to a different one.
        table = [
            model_row("gpt-4o", points_at("develiteaai"), project_id=CALLER_PROJECT_ID),
            model_row("gpt-4o", points_at("gone"), project_id=CALLER_PROJECT_ID),
            credential_row("develiteaai", "open_ai", project_id=CALLER_PROJECT_ID),
        ]
        #
        assert ask(table)[0] is None
