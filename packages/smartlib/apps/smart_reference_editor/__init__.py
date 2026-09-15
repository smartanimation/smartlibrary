"""Casting comparison and production reference replacement for Maya."""


def show(config_dir=None, parent=None):
    from .ui import show as show_window
    return show_window(config_dir=config_dir, parent=parent)
