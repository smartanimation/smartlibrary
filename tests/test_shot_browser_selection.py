"""Exercise the real navigation methods without loading project data or Maya."""
import ast
from pathlib import Path
from types import MethodType, SimpleNamespace


def navigation():
    source = Path(__file__).parents[1] / "scripts" / "shot_manager_ui.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "ShotManagerWindow")
    names = {"show_current_shot", "show_detail_mode", "open_selected_detail"}
    scope = {}
    exec(compile(ast.Module(
        body=[node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name in names],
        type_ignores=[],
    ), str(source), "exec"), scope)
    calls = []
    browser, detail = object(), object()
    state = {"page": browser, "shot": "c001", "sequence": None}
    ui = SimpleNamespace(
        shot_context=SimpleNamespace(select_shot=lambda shot: state.update(shared_shot=shot)),
        main_stack=SimpleNamespace(
            currentWidget=lambda: state["page"],
            setCurrentWidget=lambda page: state.update(page=page),
        ),
        shot_detail_page=detail,
        current_identity=lambda: state["shot"],
        current_sequence_identity=lambda: state["sequence"],
        _show_current_shot_identity=lambda shot: calls.append(("shot", shot)),
        _show_current_sequence=lambda seq: calls.append(("sequence", seq)),
        shot_list=SimpleNamespace(
            setCurrentItem=lambda item: state.update(shot=item),
        ),
    )
    for name in names:
        setattr(ui, name, MethodType(scope[name], ui))
    return ui, state, calls


def test_browser_selection_does_not_load_hidden_details():
    ui, state, calls = navigation()
    for shot in ("c001", "c002", "c003"):
        state["shot"] = shot
        ui.show_current_shot()
    assert not calls
    assert state["shared_shot"] == "c003"
    assert ui.active_shot_identity == "c003"
    assert ui.active_sequence_identity is None
    state.update(shot=None, sequence="s027")
    ui.show_current_shot()
    assert not calls
    assert ui.active_sequence_identity == "s027"
    assert ui.active_shot_identity is None
    assert state["shared_shot"] is None


def test_activation_loads_selected_shot_once():
    ui, state, calls = navigation()
    ui.show_current_shot()
    ui.open_selected_detail("c003")
    assert calls == [("shot", "c003")]
    assert state["page"] is ui.shot_detail_page


def test_sequence_activation_and_visible_detail_refresh():
    ui, state, calls = navigation()
    state.update(shot=None, sequence="s027")
    ui.show_detail_mode()
    assert calls == [("sequence", "s027")]
    ui.show_current_shot()
    assert calls == [("sequence", "s027"), ("sequence", "s027")]
