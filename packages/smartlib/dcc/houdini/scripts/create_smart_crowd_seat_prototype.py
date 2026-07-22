"""Create the Houdini network shell for the Smart Crowd seat prototype."""

from __future__ import annotations


def main() -> None:
    import hou

    from smartlib.dcc.houdini.crowd_kinefx import create_single_agent_seat_prototype

    crowd_dir = globals().get("SMART_CROWD_DIR")
    if not crowd_dir:
        crowd_dir = hou.ui.selectFile(
            title="Select Smart Crowd prototype folder",
            file_type=hou.fileType.Directory,
            chooser_mode=hou.fileChooserMode.Read,
        )
    if not crowd_dir:
        return

    crowd_dir = hou.expandString(str(crowd_dir))
    result = create_single_agent_seat_prototype(crowd_dir, replace_existing=False)
    print("Created Smart Crowd seat prototype:")
    print("  {}".format(result["node"]))
    print("Behavior:")
    print("  {}".format(" -> ".join(result["plan"]["goal"]["steps"])))
    print("Target:")
    print("  {}".format(result["plan"]["goal"]["interaction_point_id"]))


main()
