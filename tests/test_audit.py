from pathlib import Path

from site_tools.audit import audit, parse_translation_dictionary
from site_tools.build import build
from tests.conftest import SITE_URL


def build_and_audit(project: Path, edit=None):
    index = project / "site" / "index.html"
    if edit:
        index.write_text(edit(index.read_text(encoding="utf-8")), encoding="utf-8")
    build(project / "site", project / "dist", SITE_URL)
    return audit(project / "dist", SITE_URL)


def checks(result, severity):
    return {f.check for f in result.findings if f.severity == severity}


def test_current_site_has_no_errors_and_full_translation(project: Path):
    result = build_and_audit(project)
    assert result.count("error") == 0, result.to_markdown()
    assert result.untranslated == []
    assert result.facts["metrics"] == ["12", "4", "400", "300", "2", "1", "1", "4"]
    assert result.facts["contact_links"] == ["https://wa.me/971501924021", "mailto:Ahmed@arks.ae", "tel:+971501924021"]


def test_missing_canonical_is_an_error(project: Path):
    result = build_and_audit(project, lambda html: html.replace('<link rel="canonical" href="{{SITE_URL}}">\n', ""))
    assert "canonical" in checks(result, "error")


def test_invalid_structured_data_is_an_error(project: Path):
    result = build_and_audit(project, lambda html: html.replace('"@context": "https://schema.org",', '"@context": ,', 1))
    assert "structured-data" in checks(result, "error")


def test_noindex_on_the_home_page_is_an_error(project: Path):
    result = build_and_audit(project, lambda html: html.replace('content="index, follow,', 'content="noindex, follow,', 1))
    assert {"robots-meta", "sitemap"} <= checks(result, "error")


def test_image_without_alt_is_an_error(project: Path):
    result = build_and_audit(project, lambda html: html.replace(' alt="Ahmed Abouseif in a navy suit"', "", 1))
    assert "img-alt" in checks(result, "error")


def test_new_english_text_without_arabic_is_reported(project: Path):
    result = build_and_audit(project, lambda html: html.replace("<h2>Clarity under pressure.</h2>", "<h2>Calm under pressure.</h2>"))
    assert "index.html: Calm under pressure." in result.untranslated
    assert "arabic-translation" in checks(result, "warning")


def test_changed_metric_changes_the_facts(project: Path):
    result = build_and_audit(project, lambda html: html.replace('data-count="400">400<', 'data-count="500">500<'))
    assert result.facts["metrics"] == ["12", "4", "500", "300", "2", "1", "1", "4"]


def test_dictionary_parser_handles_escaped_quotes():
    html = "<script>\n    const arabic = {\n      'It\\'s':'نعم','A & B':'أ و ب'\n    };\n</script>"
    assert parse_translation_dictionary(html) == {"It's": "نعم", "A & B": "أ و ب"}
