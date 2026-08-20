import os
import time
import microcontroller

from relic_menumanager import Action, Group
from relic_menumanager.character_lcd import Character_LCD_Menu

import hardware
import menu
import settings

hardware.init()

def enter_bootloader() -> None:
    menu.write_message("Loading...")
    microcontroller.on_next_reset(microcontroller.RunMode.BOOTLOADER)
    microcontroller.reset()

def reset_device() -> None:
    menu.write_message("Resetting...")
    microcontroller.on_next_reset(microcontroller.RunMode.NORMAL)
    microcontroller.reset()

files = tuple(filter(lambda filename: not filename.startswith(".") and filename.endswith(".py"), os.listdir(menu.APP_DIR)))

apps = []
for filename in files:
    title = filename[:-3]
    title = title.lower().replace('_', ' ').split()
    for i in range(len(title)):
        title[i] = title[i][0].upper() + title[i][1:]
    title = " ".join(title)
    apps.append(Action(title, lambda filename=filename: menu.load_app(filename)))

lcd_menu = Character_LCD_Menu(hardware.lcd, hardware.COLUMNS, hardware.ROWS, "Launcher", (
    Group("Apps", tuple(apps)),
    settings.group(),
    Group("Tools", (
        Action("Bootloader", enter_bootloader),
        Action("Reset", reset_device),
    ))
))

while True:
    menu.handle_controls(lcd_menu)
    time.sleep(hardware.TASK_SLEEP)
