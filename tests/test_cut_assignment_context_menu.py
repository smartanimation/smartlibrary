from types import SimpleNamespace
from unittest.mock import Mock

from smartlib.dcc.resolve import cut_assignment_ui as ui


def dialog():
    instance = ui.CutAssignmentDialog.__new__(ui.CutAssignmentDialog)
    instance.tree = Mock()
    instance.tree.selection.return_value = ("0", "2")
    instance.context_menu = Mock()
    instance.rows = [{"production_sequence": "s001"} for _ in range(3)]
    instance.window = Mock()
    instance.refresh = Mock()
    return instance


def test_right_click_keeps_existing_multi_selection():
    instance = dialog()
    instance.tree.identify_row.return_value = "2"
    assert instance.show_context_menu(SimpleNamespace(y=20, x_root=100, y_root=200)) == "break"
    instance.tree.selection_set.assert_not_called()
    instance.context_menu.tk_popup.assert_called_once_with(100, 200)
    instance.context_menu.grab_release.assert_called_once()


def test_right_click_unselected_row_selects_only_that_row():
    instance = dialog()
    instance.tree.identify_row.return_value = "1"
    instance.show_context_menu(SimpleNamespace(y=20, x_root=100, y_root=200))
    instance.tree.selection_set.assert_called_once_with("1")


def test_right_click_empty_space_does_not_open_menu():
    instance = dialog()
    instance.tree.identify_row.return_value = ""
    instance.show_context_menu(SimpleNamespace(y=20))
    instance.context_menu.tk_popup.assert_not_called()


def test_sequence_popup_updates_only_selected_rows(monkeypatch):
    instance = dialog()
    ask = Mock(return_value=" s002 ")
    monkeypatch.setattr(ui.simpledialog, "askstring", ask)
    instance.assign_sequence()
    assert [row["production_sequence"] for row in instance.rows] == ["s002", "s001", "s002"]
    assert ask.call_args.kwargs["initialvalue"] == "s001"
    instance.refresh.assert_called_once()


def test_sequence_popup_cancel_preserves_rows(monkeypatch):
    instance = dialog()
    monkeypatch.setattr(ui.simpledialog, "askstring", Mock(return_value=None))
    instance.assign_sequence()
    assert all(row["production_sequence"] == "s001" for row in instance.rows)
    instance.refresh.assert_not_called()
