# SPDX-FileCopyrightText: Copyright (c) 2024 Cooper Dalrymple
#
# SPDX-License-Identifier: Unlicense

import os
import json
from relic_menumanager import Group, Number, Bool, Action
import menu

PATH = "/settings.json"

midi_channel = None
midi_thru = False
midi_touch_out = False

keyboard_touch = True

def _format_name(name:str) -> str:
    return name.lower().replace(' ', '_')

try:
    os.stat(PATH)
    with open(PATH, "r") as file:
        data = json.load(file)
    if type(data) is dict and data:
        for i, (key, items) in enumerate(data.items()):
            if type(items) is dict:
                for j, (name, value) in enumerate(items.items()):
                    name = _format_name(key + "_" + name)
                    if name in globals():
                        if type(globals()[name]) is bool:
                            value = bool(value)
                        globals()[name] = value
except (OSError, ValueError) as e:
    print(e)
    pass

_group = None

def save() -> bool:
    global _group
    if not isinstance(_group, Group):
        return False
    menu.write_message("Saving...")
    result = _group.write("/settings.json")
    menu.write_message("Complete!" if result else "Failed!", True)
    return result

def group() -> Group:
    global _group
    if _group is None:
        _group = Group("Settings", (
            Group("MIDI", (
                Number(
                    title="Channel",
                    default=0 if midi_channel is None else midi_channel,
                    step=1,
                    minimum=0,
                    maximum=16,
                    on_update=lambda value, item: menu.set_global_attribute(None if value == 0 else value, 'midi_channel')
                ),
                Bool(
                    title="Thru",
                    default=midi_thru,
                    on_update=lambda value, item: menu.set_global_attribute(value, 'midi_thru'),
                ),
                Bool(
                    title="Touch Out",
                    default=midi_touch_out,
                    on_update=lambda value, item: menu.set_global_attribute(value, 'midi_touch_out'),
                ),
            )),
            Group("Keyboard", tuple([
                Bool(
                    title="Touch",
                    default=keyboard_touch,
                    on_update=lambda value, item: menu.set_global_attribute(value, 'keyboard_touch'),
                ),
            ])),
            Action("Save", save),
        ))
    return _group
