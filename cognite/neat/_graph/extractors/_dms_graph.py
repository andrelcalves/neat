from collections.abc import Iterable, Sequence

from cognite.client import data_modeling as dm
from cognite.client.exceptions import CogniteAPIError
from cognite.client.utils.useful_types import SequenceNotStr
from rdflib import Namespace, URIRef

from cognite.neat._client import NeatClient
from cognite.neat._constants import COGNITE_SPACES, DEFAULT_NAMESPACE
from cognite.neat._issues import IssueList, NeatIssue, catch_warnings
from cognite.neat._issues.warnings import CDFAuthWarning, ResourceNotFoundWarning, ResourceRetrievalWarning
from cognite.neat._rules.importers import DMSImporter
from cognite.neat._rules.models import DMSRules, InformationRules
from cognite.neat._rules.models.data_types import Json
from cognite.neat._rules.models.entities import UnknownEntity
from cognite.neat._rules.models.information import InformationProperty
from cognite.neat._rules.transformers import DMSToInformation, VerifyDMSRules
from cognite.neat._shared import Triple

from ._base import KnowledgeGraphExtractor
from ._dms import DMSExtractor


class DMSGraphExtractor(KnowledgeGraphExtractor):
    def __init__(
        self,
        data_model: dm.DataModel[dm.View],
        client: NeatClient,
        namespace: Namespace = DEFAULT_NAMESPACE,
        issues: Sequence[NeatIssue] | None = None,
        instance_space: str | SequenceNotStr[str] | None = None,
        skip_cognite_views: bool = True,
        unpack_json: bool = False,
        str_to_ideal_type: bool = False,
    ) -> None:
        self._client = client
        self._data_model = data_model
        self._namespace = namespace or DEFAULT_NAMESPACE
        self._issues = IssueList(issues)
        self._instance_space = instance_space
        self._skip_cognite_views = skip_cognite_views
        self._unpack_json = unpack_json
        self._str_to_ideal_type = str_to_ideal_type

        self._views: list[dm.View] | None = None
        self._information_rules: InformationRules | None = None
        self._dms_rules: DMSRules | None = None

    @classmethod
    def from_data_model_id(
        cls,
        data_model_id: dm.DataModelIdentifier,
        client: NeatClient,
        namespace: Namespace = DEFAULT_NAMESPACE,
        instance_space: str | SequenceNotStr[str] | None = None,
        skip_cognite_views: bool = True,
        unpack_json: bool = False,
        str_to_ideal_type: bool = False,
    ) -> "DMSGraphExtractor":
        issues: list[NeatIssue] = []
        try:
            data_model = client.data_modeling.data_models.retrieve(data_model_id, inline_views=True)
        except CogniteAPIError as e:
            issues.append(CDFAuthWarning("retrieving data model", str(e)))
            return cls(
                cls._create_empty_model(dm.DataModelId.load(data_model_id)),
                client,
                namespace,
                issues,
                instance_space,
                skip_cognite_views,
                unpack_json,
                str_to_ideal_type,
            )
        if not data_model:
            issues.append(ResourceRetrievalWarning(frozenset({data_model_id}), "data model"))
            return cls(
                cls._create_empty_model(dm.DataModelId.load(data_model_id)),
                client,
                namespace,
                issues,
                instance_space,
                skip_cognite_views,
                unpack_json,
                str_to_ideal_type,
            )
        return cls(
            data_model.latest_version(),
            client,
            namespace,
            issues,
            instance_space,
            skip_cognite_views,
            unpack_json,
            str_to_ideal_type,
        )

    @classmethod
    def _create_empty_model(cls, data_model_id: dm.DataModelId) -> dm.DataModel:
        return dm.DataModel(
            data_model_id.space,
            data_model_id.external_id,
            data_model_id.version or "MISSING",
            is_global=False,
            last_updated_time=0,
            created_time=0,
            description=None,
            name=None,
            views=[],
        )

    @property
    def _model_views(self) -> list[dm.View]:
        if self._views is None:
            self._views = self._get_views()
        return self._views

    @property
    def description(self) -> str:
        return "Extracts a data model with nodes and edges."

    @property
    def source_uri(self) -> URIRef:
        space, external_id, version = self._data_model.as_id().as_tuple()
        return DEFAULT_NAMESPACE[f"{self._client.config.project}/{space}/{external_id}/{version}"]

    def extract(self) -> Iterable[Triple]:
        """Extracts the knowledge graph from the data model."""
        views = self._model_views
        if self._skip_cognite_views:
            views = [view for view in views if view.space not in COGNITE_SPACES]

        yield from DMSExtractor.from_views(
            self._client,
            views,
            instance_space=self._instance_space,
            unpack_json=self._unpack_json,
            str_to_ideal_type=self._str_to_ideal_type,
        ).extract()

    def _get_views(self) -> list[dm.View]:
        view_by_id: dict[dm.ViewId, dm.View] = {}
        if view_ids := [view_id for view_id in self._data_model.views if isinstance(view_id, dm.ViewId)]:
            try:
                # MyPy does not understand the isinstance check above.
                retrieved = self._client.data_modeling.views.retrieve(ids=view_ids)  #  type: ignore[arg-type]
            except CogniteAPIError as e:
                self._issues.append(CDFAuthWarning("retrieving views", str(e)))
            else:
                view_by_id.update({view.as_id(): view for view in retrieved})
        views: list[dm.View] = []
        data_model_id = self._data_model.as_id()
        for dm_view in self._data_model.views:
            if isinstance(dm_view, dm.View):
                views.append(dm_view)
            elif isinstance(dm_view, dm.ViewId):
                if view := view_by_id.get(dm_view):
                    views.append(view)
                else:
                    self._issues.append(ResourceNotFoundWarning(dm_view, "view", data_model_id, "data model"))
        return views

    def get_information_rules(self) -> InformationRules:
        """Returns the information rules that the extractor uses."""
        if self._information_rules is None:
            self._information_rules, self._dms_rules = self._create_rules()
        return self._information_rules

    def get_dms_rules(self) -> DMSRules:
        """Returns the DMS rules that the extractor uses."""
        if self._dms_rules is None:
            self._information_rules, self._dms_rules = self._create_rules()
        return self._dms_rules

    def get_issues(self) -> IssueList:
        """Returns the issues that occurred during the extraction."""
        return self._issues

    def _create_rules(self) -> tuple[InformationRules, DMSRules]:
        # The DMS and Information rules must be created together to link them property.
        importer = DMSImporter.from_data_model(self._client, self._data_model)
        unverified_dms = importer.to_rules()
        if self._unpack_json and (dms_rules := unverified_dms.rules):
            # Drop the JSON properties from the DMS rules as these are no longer valid.
            json_name = Json().name  # To avoid instantiating Json multiple times.
            dms_rules.properties = [
                prop
                for prop in dms_rules.properties
                if not (
                    (
                        isinstance(prop.value_type, Json)
                        or (isinstance(prop.value_type, str) and prop.value_type == json_name)
                    )
                    # We are not unpacking list of JSONs.
                    and prop.is_list is not True
                )
            ]

        with catch_warnings() as issues:
            # Any errors occur will be raised and caught outside the extractor.
            verified_dms = VerifyDMSRules(client=self._client).transform(unverified_dms)
            information_rules = DMSToInformation(self._namespace).transform(verified_dms)

        # We need to sync the metadata between the two rules, such that the `.sync_with_info_rules` method works.
        information_rules.metadata.physical = verified_dms.metadata.identifier
        verified_dms.metadata.logical = information_rules.metadata.identifier
        verified_dms.sync_with_info_rules(information_rules)

        # Adding startNode and endNode to the information rules for views that are used for edges.
        classes_by_prefix = {cls_.class_.prefix: cls_ for cls_ in information_rules.classes}
        for view in self._model_views:
            if view.used_for == "edge" and view.external_id in classes_by_prefix:
                cls_ = classes_by_prefix[view.external_id]
                for property_ in ("startNode", "endNode"):
                    information_rules.properties.append(
                        InformationProperty(
                            class_=cls_.class_,
                            property_=property_,
                            value_type=UnknownEntity(),
                            min_count=0,
                            max_count=1,
                        )
                    )

        self._issues.extend(issues)
        return information_rules, verified_dms
