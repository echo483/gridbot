"""Knowledge graph: the two extractor defects, and the no-guessing contract.

The graph's only value is that its edges can be trusted, so the tests that
matter are the ones about what it REFUSES to assert.
"""

from __future__ import annotations

import json

import pytest

from gridbot.kg import extract
from gridbot.kg.schema import METHODS, Edge, Graph, Node
from gridbot.kg.store import write_graph


def _registry(tmp_path, ids):
    p = tmp_path / "registry.jsonl"
    p.write_text("".join(
        json.dumps({"hypothesis_id": i, "run_ts": "2026-09-08T00:00:00Z",
                    "status": "REGISTERED_NO_RUN", "hypothesis_text": ""}) + "\n"
        for i in ids))
    return p


def _doc(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


class TestDocCoverage:
    """THE DEFECT. DOC_DIRS listed three subdirectories and missed 23 of 95
    files (24%) — including STATE.md, the orientation baseline and the most-cited
    document in the repo, plus all of docs/experiments/ where pre-registration
    drafts live."""

    def test_top_level_docs_are_read(self, tmp_path):
        reg = extract.extract_registry(_registry(tmp_path, ["H-A1"]))
        _doc(tmp_path, "docs/STATE.md", "the lane H-A1 is closed")
        g = extract.extract_docs(tmp_path, reg, None)
        assert "doc:docs/STATE.md" in g.nodes
        assert any(e.dst == "hypothesis:H-A1" for e in g.edges)

    def test_experiments_are_read(self, tmp_path):
        reg = extract.extract_registry(_registry(tmp_path, ["H-A1"]))
        _doc(tmp_path, "docs/experiments/DRAFT-x.md", "proposes H-A1")
        g = extract.extract_docs(tmp_path, reg, None)
        assert "doc:docs/experiments/DRAFT-x.md" in g.nodes

    def test_nested_dirs_are_read(self, tmp_path):
        reg = extract.extract_registry(_registry(tmp_path, ["H-A1"]))
        _doc(tmp_path, "docs/analysis/managed/2026-08-25/analysis.md", "H-A1")
        g = extract.extract_docs(tmp_path, reg, None)
        assert "doc:docs/analysis/managed/2026-08-25/analysis.md" in g.nodes

    def test_the_directory_rides_on_the_node_so_a_consumer_can_filter(self, tmp_path):
        """Archive is INCLUDED — knowing a lane is archived is the point — but
        it must be filterable rather than pre-filtered."""
        reg = extract.extract_registry(_registry(tmp_path, ["H-A1"]))
        _doc(tmp_path, "docs/archive/old.md", "H-A1")
        g = extract.extract_docs(tmp_path, reg, None)
        assert g.nodes["doc:docs/archive/old.md"].attrs["dir"] == "docs/archive"

    def test_a_missing_docs_tree_is_empty_not_an_error(self, tmp_path):
        reg = extract.extract_registry(_registry(tmp_path, ["H-A1"]))
        assert extract.extract_docs(tmp_path, reg, None).nodes == {}


class TestBareNoteResolution:
    """Prose writes NOTE9; the registry writes H-MAPENTRY1-NOTE9."""

    def test_an_unambiguous_bare_note_resolves(self, tmp_path):
        reg = extract.extract_registry(_registry(tmp_path, ["H-A1-NOTE9"]))
        _doc(tmp_path, "docs/d.md", "the fill model (NOTE9) says otherwise")
        g = extract.extract_docs(tmp_path, reg, None)
        e = [x for x in g.edges if x.kind == "cites"]
        assert len(e) == 1
        assert e[0].dst == "note:H-A1-NOTE9"
        assert e[0].method == "bare_note"
        assert e[0].attrs["anchor"] == "NOTE9"

    def test_an_AMBIGUOUS_bare_note_resolves_to_NOTHING(self, tmp_path):
        """NOTE1/2/3 are each shared across lanes. A wrong edge is worse than a
        missing one in a graph whose whole value is trustworthy edges."""
        reg = extract.extract_registry(_registry(tmp_path, ["H-A1-NOTE1", "H-B2-NOTE1"]))
        _doc(tmp_path, "docs/d.md", "see NOTE1 for the reason")
        g = extract.extract_docs(tmp_path, reg, None)
        assert [x for x in g.edges if x.kind == "cites"] == []

    def test_a_bare_note_never_duplicates_an_exact_match(self, tmp_path):
        reg = extract.extract_registry(_registry(tmp_path, ["H-A1-NOTE9"]))
        _doc(tmp_path, "docs/d.md", "H-A1-NOTE9 and later just NOTE9 again")
        g = extract.extract_docs(tmp_path, reg, None)
        cites = [x for x in g.edges if x.kind == "cites"]
        assert len(cites) == 1 and cites[0].method == "exact"

    def test_an_unregistered_bare_note_produces_no_edge(self, tmp_path):
        reg = extract.extract_registry(_registry(tmp_path, ["H-A1-NOTE9"]))
        _doc(tmp_path, "docs/d.md", "NOTE42 does not exist")
        g = extract.extract_docs(tmp_path, reg, None)
        assert [x for x in g.edges if x.kind == "cites"] == []

    def test_a_word_containing_note_is_not_a_reference(self, tmp_path):
        reg = extract.extract_registry(_registry(tmp_path, ["H-A1-NOTE9"]))
        _doc(tmp_path, "docs/d.md", "FOOTNOTE9 and NOTE9X are not citations")
        g = extract.extract_docs(tmp_path, reg, None)
        assert [x for x in g.edges if x.kind == "cites"] == []

    def test_the_index_drops_only_the_colliding_suffix(self):
        idx = extract._bare_note_index({"H-A-NOTE1", "H-B-NOTE1", "H-A-NOTE9"})
        assert idx == {"NOTE9": "H-A-NOTE9"}


class TestNoGuessing:
    def test_the_method_set_is_closed(self):
        with pytest.raises(ValueError, match="unknown method"):
            Edge(kind="cites", src="doc:a", dst="note:b",
                 method="inferred", source="x")

    def test_bare_note_is_an_admitted_method(self):
        assert "bare_note" in METHODS
        assert "inferred" not in METHODS

    def test_edge_endpoints_must_be_node_ids(self):
        with pytest.raises(ValueError, match="node ids"):
            Edge(kind="cites", src="a", dst="b", method="exact", source="x")


class TestStore:
    def test_a_rebuild_that_changed_nothing_is_byte_identical(self, tmp_path):
        g = Graph()
        for k in ("b", "a"):
            g.add_node(Node(kind="doc", key=k))
        g.add_edge(Edge(kind="cites", src="doc:a", dst="doc:b",
                        method="exact", source="s"))
        write_graph(g, tmp_path / "one")
        write_graph(g, tmp_path / "two")
        for name in ("nodes.jsonl", "edges.jsonl"):
            assert (tmp_path / "one" / name).read_bytes() == \
                   (tmp_path / "two" / name).read_bytes()

    def test_nodes_are_written_sorted(self, tmp_path):
        g = Graph()
        for k in ("z", "a", "m"):
            g.add_node(Node(kind="doc", key=k))
        write_graph(g, tmp_path / "g")
        keys = [json.loads(x)["key"]
                for x in (tmp_path / "g" / "nodes.jsonl").read_text().splitlines()]
        assert keys == sorted(keys)


class TestTraceabilityAudit:
    """Question 1 of the schema docstring: is this number traceable?

    A registry row cites a doc; the doc names the script that produced it. A row
    CLAIMING a number whose docs name no script states something nobody can
    re-derive — NOTE8's rule applied to the record itself.
    """

    def _graph(self, *, declares, cited=True, doc_names_script=True):
        from gridbot.kg.query import orphans
        g = Graph()
        g.add_node(Node(kind="note", key="H-A1-NOTE9",
                        attrs={"declares_instrument": declares}))
        g.add_node(Node(kind="doc", key="docs/d.md"))
        g.add_node(Node(kind="script", key="scripts/x.py"))
        if cited:
            g.add_edge(Edge(kind="cites", src="doc:docs/d.md", dst="note:H-A1-NOTE9",
                            method="exact", source="docs/d.md"))
        if doc_names_script:
            g.add_edge(Edge(kind="generated_by", src="doc:docs/d.md",
                            dst="script:scripts/x.py", method="exact",
                            source="docs/d.md"))
        return orphans(g)["rows_whose_docs_name_no_script"]

    def test_a_claiming_row_whose_doc_names_no_script_is_FLAGGED(self):
        """THE CHECK MUST BE ABLE TO FIRE — an audit that can only return empty
        is indistinguishable from one that is broken."""
        assert self._graph(declares=True, doc_names_script=False) == ["H-A1-NOTE9"]

    def test_a_claiming_row_with_a_named_script_is_clean(self):
        assert self._graph(declares=True, doc_names_script=True) == []

    def test_a_row_claiming_NO_instrument_is_not_flagged(self):
        """Policy rows — EXECUTION_RULE_CLARIFIED, BAR_RESTRUCTURE_PRE_REGISTERED
        — pin definitions rather than numbers. Listing them would be noise that
        trains a reader to ignore the list."""
        assert self._graph(declares=False, doc_names_script=False) == []

    def test_a_row_with_no_citing_doc_is_reported_by_the_other_check_only(self):
        """Not double-counted: it already appears under
        hypotheses_with_no_citing_doc."""
        assert self._graph(declares=True, cited=False) == []

    def test_the_extractor_records_whether_a_row_declares_an_instrument(self, tmp_path):
        p = tmp_path / "registry.jsonl"
        p.write_text(
            json.dumps({"hypothesis_id": "H-A1", "run_ts": "2026-09-08T00:00:00Z",
                        "status": "X", "hypothesis_text": "",
                        "instrument": "scripts/x.py"}) + "\n"
            + json.dumps({"hypothesis_id": "H-B2", "run_ts": "2026-09-08T00:00:00Z",
                          "status": "X", "hypothesis_text": ""}) + "\n")
        g = extract.extract_registry(p)
        assert g.nodes["hypothesis:H-A1"].attrs["declares_instrument"] is True
        assert g.nodes["hypothesis:H-B2"].attrs["declares_instrument"] is False
