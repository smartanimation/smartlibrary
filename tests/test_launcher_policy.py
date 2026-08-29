from smartlib.apps.launcher.main import allowed_launcher_tools


def test_internal_launcher_defaults_to_all_tools():
    assert allowed_launcher_tools({"studio": {"role": "internal"}}) is None


def test_vendor_launcher_defaults_to_smart_delivery_only():
    assert allowed_launcher_tools({"studio": {"role": "vendor"}}) == {
        "smart_delivery"
    }


def test_launcher_uses_explicit_tool_allowlist():
    assert allowed_launcher_tools(
        {
            "studio": {"role": "vendor"},
            "launcher": {"allowed_tools": ["smart_delivery", "smart_review"]},
        }
    ) == {"smart_delivery", "smart_review"}
