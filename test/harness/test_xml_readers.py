"""Layer-1 tests for the plain-XML side-channel readers (no SOLIDWORKS, no real
files): assembly component tree, document metadata, applied materials.

Each test feeds synthetic XML matching the real stream schema directly to the
pure ``parse_*`` function, so the suite is fully portable (CI-safe) — same
pattern as ``test_sheets.py``.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from swformat.api.components import (  # noqa: E402
    parse_component_tree,
    parse_part_config_tree,
)
from swformat.api.cutlist_xml import parse_cutlist  # noqa: E402
from swformat.api.docprops import parse_metadata  # noqa: E402
from swformat.api.materials import parse_materials  # noqa: E402


def test_component_tree_flags_and_path_join() -> None:
    xml = (
        '<swSolidWorks>'
        '<swFile id="3" swDocType="PART" swPath="C:\\a\\arm.SLDPRT"/>'
        '<swFile id="5" swDocType="PART" swPath="C:\\a\\base.SLDPRT"/>'
        '<swModel id="8" swFileRef="3" swBoundingBox="0 0 0 0.1 0.2 0.3"/>'
        '<swModel id="9" swFileRef="5"/>'
        '<swReference swComponentName="arm" swConfigurationName="Default" '
        ' swExcludeFromBOM="YES" swFlexible="NO" swHidden="NO" swSuppressed="NO" '
        ' swIsVirtualComponent="NO" swTransform="1 0 0" swModelRef="8"/>'
        '<swReference swComponentName="base" swConfigurationName="Default" '
        ' swExcludeFromBOM="NO" swFlexible="YES" swHidden="NO" swSuppressed="NO" '
        ' swIsVirtualComponent="NO" swModelRef="9"/>'
        '</swSolidWorks>'
    )
    comps = parse_component_tree(xml)
    assert [c.name for c in comps] == ["arm", "base"]
    arm, base = comps
    assert arm.exclude_from_bom is True and arm.flexible is False
    assert arm.path == "C:\\a\\arm.SLDPRT"          # swModelRef 8 -> file 3 -> path
    assert arm.transform == "1 0 0"
    assert arm.bounding_box == "0 0 0 0.1 0.2 0.3"   # via swModelRef 8 -> swModel.swBoundingBox
    assert base.exclude_from_bom is False and base.flexible is True
    assert base.path == "C:\\a\\base.SLDPRT"        # swModelRef 9 -> file 5 -> path
    # bytes input + empty input
    assert parse_component_tree(xml.encode("latin1"))[0].name == "arm"
    assert parse_component_tree(b"") == []


def test_doc_metadata_core_and_app() -> None:
    core = (
        '<cp:coreProperties><dc:title>Widget</dc:title>'
        '<dc:creator>alice</dc:creator><cp:revision>B</cp:revision>'
        '<cp:lastModifiedBy>bob</cp:lastModifiedBy>'
        '<dcterms:created>2026-01-01T00:00:00Z</dcterms:created>'
        '<dcterms:modified>2026-02-02T00:00:00Z</dcterms:modified></cp:coreProperties>'
    )
    app = (
        '<Properties><Application>SOLIDWORKS 2026</Application>'
        '<AppVersion>34.1.0</AppVersion><Company>Acme</Company>'
        '<Template>assembly.asmdot</Template><TotalTime>42</TotalTime>'
        '<DocSecurity>0</DocSecurity></Properties>'
    )
    m = parse_metadata(core, app)
    assert m.title == "Widget" and m.creator == "alice" and m.revision == "B"
    assert m.last_modified_by == "bob"
    assert m.created.startswith("2026-01-01") and m.modified.startswith("2026-02-02")
    assert m.application == "SOLIDWORKS 2026" and m.app_version == "34.1.0"
    assert m.company == "Acme" and m.template == "assembly.asmdot"
    assert m.total_edit_minutes == "42" and m.doc_security == "0"
    # missing streams -> all None, no crash
    assert parse_metadata(b"", b"").title is None


def test_materials_utf16_and_properties() -> None:
    xml = (
        '<mstns:materials version="2008.03">'
        '<classification name="Steel">'
        '<material name="SOLIDWORKS Materials|Plain Carbon Steel" matid="9">'
        '<physicalproperties>'
        '<EX displayname="Elastic Modulus" value="210000000000.000000"/>'
        '<DENS displayname="Density" value="7800.000000"/>'
        '<SIGYLD displayname="Yield Strength" value="220594000.000000"/>'
        '</physicalproperties></material></classification></mstns:materials>'
    )
    raw = ("﻿" + xml).encode("utf-16-le")   # BOM (ff fe) + UTF-16-LE body
    mats = parse_materials(raw)
    assert len(mats) == 1
    mat = mats[0]
    assert mat.classification == "Steel"
    assert mat.name == "SOLIDWORKS Materials|Plain Carbon Steel" and mat.matid == "9"
    assert mat.properties["DENS"] == "7800.000000"
    assert mat.properties["EX"].startswith("210000000000")
    assert "SIGYLD" in mat.properties
    assert parse_materials(b"") == []


def test_part_config_tree() -> None:
    xml = (
        '<swSolidWorks swObjCount="3">'
        '<swHeader><swFile id="3" swDocType="PART" swCreationTime="1559849301" '
        ' swPath="C:\\p\\widget.SLDPRT"/></swHeader>'
        '<swModelList><swModel id="2" swName="widget" swConfigurationName="Default" '
        ' swFileRef="3"/></swModelList>'
        '<swConfigurationList>'
        '<swConfiguration swName="Default" swID="0" swMostRecentConfiguration="NO"/>'
        '<swConfiguration swName="Heavy" swID="1" swMostRecentConfiguration="YES"/>'
        '</swConfigurationList></swSolidWorks>'
    )
    info = parse_part_config_tree(xml)
    assert info.path == "C:\\p\\widget.SLDPRT"
    assert info.doc_type == "PART" and info.creation_time == "1559849301"
    assert info.configs == ["Default", "Heavy"]
    assert info.most_recent_config == "Heavy"


def test_cutlist_xml() -> None:
    xml = (
        '<Configuration id="0" Name="Default">'
        '<Feature Name="Cut-List-Item1" Type="Cut-List-Item" id="95">'
        '<CustomProperty Name="MATERIAL" Type="Text">Plain Carbon Steel</CustomProperty>'
        '<CustomProperty Name="LENGTH" Type="Text">250</CustomProperty>'
        '<Quantity id="">4</Quantity>'
        '<ExcludeFromCutlist id="">FALSE</ExcludeFromCutlist>'
        '<CutlistType id="">1</CutlistType></Feature>'
        '<Feature Name="Cut-List-Item2" Type="Cut-List-Item" id="96">'
        '<Quantity id="">2</Quantity>'
        '<ExcludeFromCutlist id="">TRUE</ExcludeFromCutlist>'
        '<CutlistType id="">1</CutlistType></Feature>'
        '</Configuration>'
    )
    items = parse_cutlist(xml)
    assert [i.feature_name for i in items] == ["Cut-List-Item1", "Cut-List-Item2"]
    a, b = items
    assert a.config == "Default" and a.quantity == "4" and a.exclude_from_cutlist is False
    assert a.properties["MATERIAL"] == "Plain Carbon Steel" and a.properties["LENGTH"] == "250"
    assert b.exclude_from_cutlist is True and b.quantity == "2"
    assert parse_cutlist(b"") == []


def test_compat_version_status_and_warning() -> None:
    import warnings

    from swformat.compat import (
        UntestedVersionWarning,
        version_status,
        warn_if_untested,
        warn_streams,
    )
    assert version_status(19000) == "tested"
    assert version_status(None) == "unsupported"
    assert version_status(13000) == "untested-modern"
    assert version_status(25000) == "untested-newer"
    # tested -> no warning; untested -> UntestedVersionWarning
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        warn_if_untested(19000)
        assert rec == []
        warn_if_untested(25000)
        assert any(issubclass(w.category, UntestedVersionWarning) for w in rec)
    # warn_streams derives version from the _MO_VERSION_* stream name
    assert warn_streams({"_MO_VERSION_19000/Biography": b"", "x": b""}) == 19000
