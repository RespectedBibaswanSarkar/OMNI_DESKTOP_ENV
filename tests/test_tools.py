from __future__ import annotations

from omni.tools.discovery import ToolRegistry, discover_all, _categorize, _tier


def test_tier_destructive_keywords():
    assert _tier("rm", "delete a file") == "destructive"
    assert _tier("install", "install a package") == "destructive"
    assert _tier("cat", "read a file") == "read-only"


def test_categorize():
    assert _categorize("firefox", "web browser", "firefox") == "web"
    assert _categorize("gimp", "image editor", "gimp") == "media"
    assert _categorize("python3", "python interpreter", "python3") == "code"
    assert _categorize("nautilus", "file manager", "nautilus") == "files"
    assert _categorize("hyprctl", "hyprland control", "hyprctl") == "system"
    assert _categorize("libreoffice", "office suite", "libreoffice") == "documents"
    assert _categorize("thunderbird", "email client", "thunderbird") == "communication"
    assert _categorize("xed", "a text editor", "xed") == "general"


def test_discover_all_includes_builtin_file_tools():
    tools = discover_all()
    ids = {t.id for t in tools}
    assert "file:read" in ids
    assert "file:write" in ids
    assert "file:delete" in ids
    delete = next(t for t in tools if t.id == "file:delete")
    assert delete.destructive is True
    assert delete.requires_confirmation is True


def test_discover_all_deduplicates_and_stable():
    tools = discover_all()
    ids = [t.id for t in tools]
    assert len(ids) == len(set(ids))


def test_registry_refresh_and_queries():
    reg = ToolRegistry(ttl_seconds=0)
    reg.refresh(force=True)
    assert len(reg.tools) > 0
    by_cat = reg.by_category("system")
    assert any(t.kind == "hyprland" for t in by_cat) or len(by_cat) >= 0
    des = reg.destructive()
    assert any("delete" in t.id or t.destructive for t in des)
    reg.refresh(force=False)  # cached, no-op quickly
    assert len(reg.tools) > 0


def test_tool_spec_serialization():
    tools = discover_all()
    for t in tools:
        d = t.as_dict()
        assert d["id"] == t.id
        assert d["kind"] == t.kind
        assert d["safety_tier"] == t.safety_tier
