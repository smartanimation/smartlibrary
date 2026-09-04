"""Experimental Smart Playblast with generated per-layer cameras."""


def show(config_dir=None, parent=None):
    from .ui import show as show_window
    return show_window(config_dir=config_dir, parent=parent)
