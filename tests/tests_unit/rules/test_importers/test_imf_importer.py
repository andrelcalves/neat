from cognite.neat._issues import catch_issues
from cognite.neat._issues.warnings._resources import ResourceRegexViolationWarning
from cognite.neat._rules import importers
from cognite.neat._rules.transformers import VerifyAnyRules
from tests.config import IMF_EXAMPLE


def test_imf_importer():
    with catch_issues() as issues:
        read_rules = importers.IMFImporter.from_file(IMF_EXAMPLE).to_rules()
        rules = VerifyAnyRules().transform(read_rules)

    regex_violations = [issue for issue in issues if isinstance(issue, ResourceRegexViolationWarning)]

    assert len(rules.classes) == 63
    assert len(rules.properties) == 62
    assert len(issues) == 207
    assert len(regex_violations) == 129
