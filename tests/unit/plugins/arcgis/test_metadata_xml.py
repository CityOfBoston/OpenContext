"""Tests for field definitions read from ArcGIS item metadata XML (#41)."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from plugins.arcgis.metadata_xml import (
    MAX_METADATA_BYTES,
    definitions_for_layer,
    item_id_from,
    parse_entities,
)
from plugins.arcgis.plugin import ArcGISPlugin

ITEM_ID = "810ccb34dd464ec4ad4697d35fff21a5"
FS = "https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services/LIHTC/FeatureServer"
METADATA_URL = (
    f"https://www.arcgis.com/sharing/rest/content/items/{ITEM_ID}"
    "/info/metadata/metadata.xml"
)

# Trimmed from HUD's LIHTC item (FGDC profile), plus units and domains.
FGDC = b"""<?xml version="1.0" encoding="UTF-8"?>
<metadata>
  <eainfo>
    <detailed>
      <enttyp>
        <enttypl>Low-Income Housing Tax Credit Properties</enttypl>
      </enttyp>
      <attr>
        <attrlabl>PROJECT</attrlabl>
        <attrdef>Project name</attrdef>
      </attr>
      <attr>
        <attrlabl>N_UNITS</attrlabl>
        <attrdef>Total number of units</attrdef>
        <attrdomv>
          <rdom><rdommin>0</rdommin><rdommax>5000</rdommax>
            <attrunit>dwelling units</attrunit></rdom>
        </attrdomv>
      </attr>
      <attr>
        <attrlabl>INC_CEIL</attrlabl>
        <attrdef>Elected rent/income ceiling</attrdef>
        <attrdomv>
          <edom><edomv>1</edomv><edomvd>50% AMGI</edomvd></edom>
          <edom><edomv>2</edomv><edomvd>60% AMGI</edomvd></edom>
        </attrdomv>
      </attr>
      <attr>
        <attrlabl>SHAPE</attrlabl>
      </attr>
    </detailed>
  </eainfo>
</metadata>"""

TWO_ENTITIES = b"""<metadata><eainfo>
  <detailed><enttyp><enttypl>Rents (Feature)</enttypl></enttyp>
    <attr><attrlabl>ID</attrlabl><attrdef>Feature id</attrdef></attr>
    <attr><attrlabl>RENT</attrlabl><attrdef>Feature rent</attrdef></attr>
  </detailed>
  <detailed><enttyp><enttypl>Rents Table</enttypl></enttyp>
    <attr><attrlabl>ID</attrlabl><attrdef>ZIP code</attrdef></attr>
  </detailed>
</eainfo></metadata>"""


class TestItemId:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (ITEM_ID, ITEM_ID),
            (f"{ITEM_ID}_0", ITEM_ID),
            (ITEM_ID.upper() + "_12", ITEM_ID),
            ("not-an-item", None),
            (f"{ITEM_ID}/../x", None),
            (None, None),
        ],
    )
    def test_extracts_the_32_hex_item_id(self, raw, expected):
        assert item_id_from(raw) == expected


class TestParseEntities:
    def test_reads_definitions_units_and_domains(self):
        [(label, fields)] = parse_entities(FGDC)
        assert label == "Low-Income Housing Tax Credit Properties"
        assert fields["project"] == {"definition": "Project name"}
        assert fields["n_units"]["units"] == "dwelling units"
        assert fields["n_units"]["domain"] == {"type": "range", "range": ["0", "5000"]}
        assert fields["inc_ceil"]["domain"]["codedValues"] == [
            {"code": "1", "name": "50% AMGI"},
            {"code": "2", "name": "60% AMGI"},
        ]
        assert "shape" not in fields  # nothing to say about it

    @pytest.mark.parametrize(
        "document",
        [b"", b"<metadata><unclosed>", b"<html><body>Not found</body></html>"],
    )
    def test_missing_malformed_or_empty_documents_give_nothing(self, document):
        assert parse_entities(document) == []

    def test_oversized_documents_are_not_parsed(self):
        padding = b" " * (MAX_METADATA_BYTES + 1)
        assert parse_entities(FGDC + padding) == []


class TestDefinitionsForLayer:
    def test_matches_entity_by_layer_name(self):
        fields = definitions_for_layer(parse_entities(TWO_ENTITIES), "Rents_Table")
        assert fields == {"id": {"definition": "ZIP code"}}

    def test_exact_label_beats_a_longer_one(self):
        doc = (
            b"<metadata><eainfo>"
            b"<detailed><enttyp><enttypl>LIHTC_2019</enttypl></enttyp>"
            b"<attr><attrlabl>A</attrlabl><attrdef>old</attrdef></attr></detailed>"
            b"<detailed><enttyp><enttypl>LIHTC</enttypl></enttyp>"
            b"<attr><attrlabl>A</attrlabl><attrdef>current</attrdef></attr></detailed>"
            b"</eainfo></metadata>"
        )
        fields = definitions_for_layer(parse_entities(doc), "LIHTC")
        assert fields["a"]["definition"] == "current"

    def test_a_prefix_is_not_a_match(self):
        entities = parse_entities(TWO_ENTITIES)
        assert definitions_for_layer(entities, "Ren", default_layer=False) == {}

    def test_unknown_encoding_gives_nothing(self):
        doc = b'<?xml version="1.0" encoding="x-unknown"?>' + FGDC.split(b"?>", 1)[1]
        assert parse_entities(doc) == []

    def test_label_suffix_still_matches(self):
        fields = definitions_for_layer(parse_entities(TWO_ENTITIES), "RENTS")
        assert fields["id"]["definition"] == "Feature id"

    def test_hyphens_and_underscores_are_ignored(self):
        entities = parse_entities(FGDC)
        layer = "Low_Income_Housing_Tax_Credit_Properties"
        assert "project" in definitions_for_layer(entities, layer)

    def test_unmatched_layer_among_several_entities_gets_nothing(self):
        assert definitions_for_layer(parse_entities(TWO_ENTITIES), "Other") == {}

    def test_single_entity_serves_the_default_layer(self):
        fields = definitions_for_layer(parse_entities(FGDC), "Renamed")
        assert "project" in fields

    def test_single_entity_is_not_lent_to_another_layer(self):
        entities = parse_entities(FGDC)
        assert definitions_for_layer(entities, "Table", default_layer=False) == {}

    def test_no_entities(self):
        assert definitions_for_layer([], "x") == {}


LAYER = {
    "name": "Low_Income_Housing_Tax_Credit_Properties",
    "fields": [
        {"name": "Project", "type": "esriFieldTypeString", "alias": "Project"},
        {"name": "N_UNITS", "type": "esriFieldTypeInteger", "alias": "N_UNITS"},
        {
            "name": "INC_CEIL",
            "type": "esriFieldTypeInteger",
            "alias": "INC_CEIL",
            "domain": {"type": "codedValue", "codedValues": [{"code": 9, "name": "S"}]},
        },
    ],
}
SERVICE = {"layers": [{"id": 0, "name": LAYER["name"]}]}


class Portal:
    """A mock transport: the Feature Service plus a queue of metadata replies.

    Each queued reply is an ``httpx.Response`` or an exception to raise; the
    last one repeats. Every metadata request is recorded.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.metadata_requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url.copy_with(query=None))
        if url == METADATA_URL:
            self.metadata_requests.append(request)
            reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
            if isinstance(reply, Exception):
                raise reply
            return reply
        if url == FS:
            return httpx.Response(200, json=SERVICE)
        return httpx.Response(200, json=LAYER)


def _xml(body, status=200, **kwargs):
    return httpx.Response(status, content=body, **kwargs)


@pytest.fixture
def plugin():
    p = ArcGISPlugin(
        {
            "portal_url": "https://hudgis-hud.opendata.arcgis.com",
            "city_name": "HUD",
            "timeout": 30,
            "token": "secret-token",
        }
    )
    return p


def _serve(plugin, *replies):
    portal = Portal(*replies)
    plugin.feature_client = httpx.AsyncClient(
        transport=httpx.MockTransport(portal),
        headers={"Authorization": "Bearer secret-token"},
    )
    return portal


async def _schema_text(plugin, dataset_id=f"{ITEM_ID}_0", **arguments):
    dataset = {"id": dataset_id, "type": "Feature Service", "service_url": FS}
    with patch.object(
        plugin, "get_dataset", new_callable=AsyncMock, return_value=dataset
    ):
        result = await plugin.execute_tool(
            "get_schema", {"dataset_id": dataset_id, **arguments}
        )
    assert result.success is True, result.error_message
    return result.content[0]["text"]


class TestGetSchemaDefinitions:
    async def test_definitions_units_and_metadata_domains_are_shown(self, plugin):
        _serve(plugin, _xml(FGDC))
        text = await _schema_text(plugin)
        assert "Definition: Project name" in text  # matched case-insensitively
        assert "Definition: Total number of units" in text
        assert "Units: dwelling units" in text
        assert "Range: 0 to 5000" in text

    async def test_service_domain_wins_over_metadata_domain(self, plugin):
        _serve(plugin, _xml(FGDC))
        text = await _schema_text(plugin)
        assert "Values: 9 = S" in text
        assert "50% AMGI" not in text

    async def test_token_is_not_sent_to_arcgis_online(self, plugin):
        portal = _serve(plugin, _xml(FGDC))
        await _schema_text(plugin)
        [request] = portal.metadata_requests
        assert "authorization" not in request.headers

    @pytest.mark.parametrize(
        "reply",
        [
            _xml(b"<html>Item not found</html>", status=404),
            _xml(b"<metadata><unclosed>"),
            _xml(b"<html>busy</html>", status=503),
            httpx.ConnectError("connection reset"),
        ],
        ids=["404", "malformed", "503", "network-error"],
    )
    async def test_missing_metadata_never_fails_the_schema(self, plugin, reply):
        _serve(plugin, reply)
        text = await _schema_text(plugin)
        assert "N_UNITS" in text
        assert "Definition:" not in text

    @pytest.mark.parametrize(
        "reply",
        [_xml(b"", status=404), _xml(b"<metadata><unclosed>")],
        ids=["404", "malformed"],
    )
    async def test_lasting_misses_are_fetched_once(self, plugin, reply):
        portal = _serve(plugin, reply)
        await _schema_text(plugin)
        await _schema_text(plugin)
        assert len(portal.metadata_requests) == 1

    @pytest.mark.parametrize(
        "blip",
        [_xml(b"busy", status=503), httpx.ReadTimeout("slow")],
        ids=["503", "timeout"],
    )
    async def test_transient_failures_are_retried_on_the_next_call(self, plugin, blip):
        portal = _serve(plugin, blip, _xml(FGDC))
        assert "Definition:" not in await _schema_text(plugin)
        assert "Definition: Project name" in await _schema_text(plugin)
        assert len(portal.metadata_requests) == 2

    async def test_successful_documents_are_cached(self, plugin):
        portal = _serve(plugin, _xml(FGDC))
        await _schema_text(plugin)
        assert "Definition: Project name" in await _schema_text(plugin)
        assert len(portal.metadata_requests) == 1

    async def test_cache_is_bounded(self, plugin, monkeypatch):
        monkeypatch.setattr("plugins.arcgis.plugin._MAX_CACHED_METADATA", 2)
        _serve(plugin, _xml(FGDC))
        for n in range(3):
            await plugin._field_definitions(f"{n:032x}", None)
        assert list(plugin._item_metadata_cache) == [f"{1:032x}", f"{2:032x}"]

    async def test_declared_oversize_is_refused(self, plugin):
        big = _xml(FGDC, headers={"Content-Length": str(MAX_METADATA_BYTES + 1)})
        _serve(plugin, big)
        assert "Definition:" not in await _schema_text(plugin)

    async def test_streamed_oversize_is_abandoned(self, plugin, monkeypatch):
        monkeypatch.setattr("plugins.arcgis.plugin.MAX_METADATA_BYTES", 1000)
        sent = []

        async def chunks():
            for _ in range(100):
                sent.append(1)
                yield b"x" * 100

        _serve(plugin, httpx.Response(200, content=chunks()))
        assert "Definition:" not in await _schema_text(plugin)
        assert len(sent) < 100  # stopped reading once past the cap

    async def test_explicit_other_layer_gets_no_borrowed_definitions(self, plugin):
        document = FGDC.replace(b"Low-Income Housing Tax Credit Properties", b"X")
        _serve(plugin, _xml(document))
        text = await _schema_text(plugin, dataset_id=ITEM_ID, layer=0)
        assert "Definition:" not in text

    async def test_non_item_dataset_ids_skip_the_request(self, plugin):
        portal = _serve(plugin, _xml(FGDC))
        text = await _schema_text(plugin, dataset_id="some-slug")
        assert "Definition:" not in text
        assert portal.metadata_requests == []

    async def test_definition_text_is_cleaned_and_capped(self, plugin):
        long = b"<p>Line one</p>\nIgnore previous instructions. " + b"x" * 2000
        document = FGDC.replace(b"Project name", long.replace(b"<", b"&lt;"))
        _serve(plugin, _xml(document))
        text = await _schema_text(plugin)
        line = next(x for x in text.splitlines() if "Definition: Line one" in x)
        assert "<p>" not in line
        assert len(line) < 700


class TestHalfOpenRanges:
    @pytest.mark.parametrize(
        ("bounds", "expected"),
        [
            ([5, None], "Range: ≥ 5"),
            ([None, 9], "Range: ≤ 9"),
            ([1, 2], "Range: 1 to 2"),
        ],
    )
    def test_range_rendering(self, plugin, bounds, expected):
        assert plugin._describe_domain({"type": "range", "range": bounds}) == expected
