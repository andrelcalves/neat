import pytest

from cognite.neat._constants import DEFAULT_NAMESPACE
from cognite.neat._graph import extractors, transformers
from cognite.neat._store import NeatGraphStore
from tests.config import CLASSIC_CDF_EXTRACTOR_DATA


def test_asset_depth_transformer_without_typing():
    store = NeatGraphStore.from_memory_store()
    store.write(extractors.AssetsExtractor.from_file(CLASSIC_CDF_EXTRACTOR_DATA / "assets.yaml"))

    transformer = transformers.AddAssetDepth()

    store.transform(transformer)

    result = list(store.dataset.query(f"SELECT ?s WHERE {{ ?s <{DEFAULT_NAMESPACE.depth}> 0}}"))

    assert len(result) == 1
    assert result[0][0] == DEFAULT_NAMESPACE.Asset_4901062138807933
    assert set(store.dataset.query(f"SELECT ?s WHERE {{ ?s <{DEFAULT_NAMESPACE.depth}> ?d}}")) == set(
        store.dataset.query(f"SELECT ?s WHERE {{ ?s a <{DEFAULT_NAMESPACE.Asset}>}}")
    )


def test_asset_depth_transformer_with_typing():
    store = NeatGraphStore.from_memory_store()
    extractor = extractors.AssetsExtractor.from_file(CLASSIC_CDF_EXTRACTOR_DATA / "assets.yaml")
    store.write(extractor)

    transformer = transformers.AddAssetDepth(
        depth_typing={
            0: "RootCimNode",
            1: "GeographicalRegion",
            2: "SubGeographicalRegion",
            3: "Substation",
        }
    )

    store.transform(transformer)

    assert set(store.queries.summarize_instances()) == {
        ("GeographicalRegion", 1),
        ("RootCimNode", 1),
        ("SubGeographicalRegion", 1),
        ("Substation", 1),
    }


def test_asset_depth_transformer_warning():
    store = NeatGraphStore.from_memory_store()

    transformer = transformers.AddAssetDepth()
    with pytest.warns(
        UserWarning,
        match="Cannot transform graph store with AddAssetDepth, missing one or more required changes",
    ):
        store.transform(transformer)

    extractor = extractors.AssetsExtractor.from_file(CLASSIC_CDF_EXTRACTOR_DATA / "assets.yaml")
    store.write(extractor)
    store.transform(transformer)

    with pytest.warns(
        UserWarning,
        match="Cannot transform graph store with AddAssetDepth, already applied",
    ):
        store.transform(transformer)
