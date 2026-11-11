#!/usr/bin/env python3
"""Writes gnome45/extension.js from gnome42/extension.js.

GNOME 45 changed how extensions load their parts and start up, but the menu
itself is the same. Edit gnome42/extension.js, then run this; the tests check
that the two match.
"""

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent

REPLACEMENTS = [
    ("""/* Dimly's top-bar menu: brightness and contrast sliders and presets for
 * external monitors.
""", """/* Dimly's top-bar menu for GNOME 45 and later: brightness and contrast sliders
 * and presets for external monitors. Generated from gnome42/extension.js by
 * make-gnome45.py: edit that file instead.
"""),
    ("""'use strict';

const { Clutter, Gio, GLib, GObject, Shell, St } = imports.gi;
const Main = imports.ui.main;
const PanelMenu = imports.ui.panelMenu;
const PopupMenu = imports.ui.popupMenu;
const Slider = imports.ui.slider;
const ExtensionUtils = imports.misc.extensionUtils;

const Me = ExtensionUtils.getCurrentExtension();
""", """import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as Slider from 'resource:///org/gnome/shell/ui/slider.js';

// Set when the extension is enabled; the icons live next to this file.
let extensionPath = null;
"""),
    ("${Me.path}/icons/", "${extensionPath}/icons/"),
    ("class Extension {\n    enable() {\n",
     "export default class DimlyExtension extends Extension {\n    enable() {\n        extensionPath = this.path;\n"),
    ("Main.panel.addToStatusArea(Me.metadata.uuid,", "Main.panel.addToStatusArea(this.uuid,"),
    ("""

function init() {
    return new Extension();
}""", ""),
]


def generate():
    text = (HERE / "gnome42" / "extension.js").read_text()
    for old, new in REPLACEMENTS:
        if text.count(old) != 1:
            sys.exit(f"make-gnome45.py: expected exactly one of {old[:50]!r} in gnome42/extension.js")
        text = text.replace(old, new)
    if "imports." in text or "Me." in text:
        sys.exit("make-gnome45.py: GNOME 42-only code is left over; add a replacement for it")
    return text


if __name__ == "__main__":
    (HERE / "gnome45" / "extension.js").write_text(generate())
    print("Wrote gnome45/extension.js")
