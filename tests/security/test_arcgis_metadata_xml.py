"""Security tests for parsing ArcGIS item metadata XML (XXE, entity expansion)."""

import pytest

from plugins.arcgis.metadata_xml import parse_entities

pytestmark = pytest.mark.security

XXE = b"""<?xml version="1.0"?>
<!DOCTYPE metadata [<!ENTITY secret SYSTEM "file:///etc/passwd">]>
<metadata><eainfo><detailed>
  <attr><attrlabl>A</attrlabl><attrdef>&secret;</attrdef></attr>
</detailed></eainfo></metadata>"""

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<metadata><eainfo><detailed>
  <attr><attrlabl>A</attrlabl><attrdef>&lol3;</attrdef></attr>
</detailed></eainfo></metadata>"""


@pytest.mark.parametrize(
    "document",
    [
        XXE,
        BILLION_LAUGHS,
        XXE.lower(),
        # The DTD is invisible to a byte-level scan in UTF-16 or UTF-32.
        XXE.replace(b'version="1.0"', b'version="1.0" encoding="UTF-16"')
        .decode()
        .encode("utf-16"),
        BILLION_LAUGHS.decode().encode("utf-16-le").join([b"\xff\xfe", b""]),
        XXE.replace(b'version="1.0"', b'version="1.0" encoding="UTF-32"')
        .decode()
        .encode("utf-32"),
    ],
    ids=["xxe", "billion-laughs", "lowercase", "utf-16", "utf-16-le-bom", "utf-32"],
)
def test_documents_with_a_dtd_are_refused_before_parsing(document):
    assert parse_entities(document) == []


def test_utf16_document_without_a_dtd_still_parses():
    document = (
        '<?xml version="1.0" encoding="UTF-16"?><metadata><eainfo><detailed>'
        "<attr><attrlabl>A</attrlabl><attrdef>Defined</attrdef></attr>"
        "</detailed></eainfo></metadata>"
    ).encode("utf-16")
    [(_, fields)] = parse_entities(document)
    assert fields["a"]["definition"] == "Defined"
