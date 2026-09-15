"""Isolated Maya entry point for Asset Manager studio FBX export."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages"))

if __name__ == "__main__":
    import maya.standalone
    maya.standalone.initialize(name="python")
    try:
        from smartlib.dcc.maya.studio_delivery import export_job
        export_job(sys.argv[1])
    finally:
        maya.standalone.uninitialize()
