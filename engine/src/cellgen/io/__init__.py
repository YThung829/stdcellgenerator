"""Reusable, dependency-free parsers for SMTCell's file formats.

These modules were originally written for the PySide6 GUI
(``src/gui/smtcell_gui/``) but contain no Qt code. They are the canonical
copies now: the GUI, the run driver, and the web backend all import from
here so the parsing rules live in exactly one place.
"""
