# SPDX-FileCopyrightText: Copyright (c) 2024 Cooper Dalrymple
#
# SPDX-License-Identifier: Unlicense

import audiomixer
import synthio
import asyncio

from adafruit_midi.note_on import NoteOn
from adafruit_midi.note_off import NoteOff

from relic_keymanager import Sequencer, TimerStep
from relic_menumanager import Group, Item, Percentage, Bool, Number, Action, List
from relic_menumanager.synthio import Sequence, Mix
from relic_menumanager.character_lcd import Character_LCD_Menu
from relic_synthvoice.percussive import *

import adafruit_midi
from adafruit_midi.note_on import NoteOn
from adafruit_midi.note_off import NoteOff
from adafruit_midi.midi_message import MIDIMessage, MIDIUnknownEvent

import hardware
import menu
import settings

if hardware.is_rp2040():
    hardware.SAMPLE_RATE = 22050
    hardware.BUFFER_SIZE = 2048

hardware.init()

## Audio

mixer = audiomixer.Mixer(
    voice_count=1,
    channel_count=hardware.CHANNELS,
    sample_rate=hardware.SAMPLE_RATE,
    buffer_size=hardware.BUFFER_SIZE,
    bits_per_sample=hardware.BITS,
    samples_signed=True,
)
hardware.audio.play(mixer)

synth = synthio.Synthesizer(
    sample_rate=hardware.SAMPLE_RATE,
    channel_count=hardware.CHANNELS,
)
mixer.voice[0].play(synth)

voices = (
    Kick(synth),
    Snare(synth),
    ClosedHat(synth),
    OpenHat(synth),
    FloorTom(synth),
    MidTom(synth),
    HighTom(synth),
    Ride(synth),
)
# No update task required

## Sequencer

sequencer = Sequencer(
    length=16,
    tracks=len(voices),
    bpm=120,
)
sequencer.steps = TimerStep.SIXTEENTH

def sequencer_press(notenum: int, velocity: float) -> None:
    voices[(notenum - 1) % len(voices)].press(velocity)

    msg = NoteOn(notenum, velocity=int(velocity * 127), channel=settings.midi_channel)
    hardware.midi_uart.send(msg, channel=msg.channel)
    hardware.midi_usb.send(msg, channel=msg.channel)
sequencer.on_press = sequencer_press

def sequencer_release(notenum):
    voice = (notenum - 1) % len(voices)
    if voice == 3: # Closed Hat
        voices[voice + 1].release() # Force release Open Hat
    voices[voice].release()

    # Send midi note off
    msg = NoteOff(notenum, channel=settings.midi_channel)
    hardware.midi_uart.send(msg, channel=msg.channel)
    hardware.midi_usb.send(msg, channel=msg.channel)
sequencer.on_release = sequencer_release

## USB & Hardware MIDI

def midi_process_message(msg: MIDIMessage) -> None:
    if settings.midi_thru and not isinstance(msg, MIDIUnknownEvent):
        hardware.midi_usb.send(msg, channel=msg.channel)
        hardware.midi_uart.send(msg, channel=msg.channel)

    if settings.midi_channel is not None and 1 <= settings.midi_channel <= 16 and msg.channel != settings.midi_channel - 1:
        return
    
    if isinstance(msg, NoteOn):
        if msg.velocity > 0.0:
            voices[(msg.note - 1) % len(voices)].press(msg.velocity)
        else:
            voices[(msg.note - 1) % len(voices)].release()

    elif isinstance(msg, NoteOff):
        voices[(msg.note - 1) % len(voices)].release()
    
def midi_process_messages(midi:adafruit_midi.MIDI, limit:int = 32) -> None:
    while limit:
        if not (msg := midi.receive()):
            break
        midi_process_message(msg)
        limit -= 1

async def midi_task() -> None:
    while True:
        midi_process_messages(hardware.midi_usb)
        midi_process_messages(hardware.midi_uart)
        await asyncio.sleep(hardware.TASK_SLEEP)

## Touch

def ttp_press(position:int) -> None:
    if not settings.keyboard_touch:
        return
    
    sequence = lcd_menu.selected
    if isinstance(sequence, Group) and not isinstance(sequence, Sequence):
        sequence = sequence.current_item
    if not isinstance(sequence, Sequence):
        return
    
    step = sequence.items[position % sequence.length]
    step.value = not step.value
    
    lcd_menu.draw()
    
    # NOTE: No touch midi out
hardware.ttp.on_press = ttp_press

async def touch_task() -> None:
    while True:
        hardware.ttp.update()
        await asyncio.sleep(hardware.TASK_SLEEP)

## Menu

def update_sequencer_length(value: int, item: Item) -> None:
    sequencer.length = value
    for sequence_item in sequence_items:
        sequence_item.length = value

def update_sequencer_track(track: int, value: tuple) -> None:
    if track > sequencer.tracks or track < 0:
        return
    for i, state in enumerate(value):
        if state and not sequencer.has_note(
            position=i,
            track=track,
        ):
            sequencer.set_note(
                position=i,
                notenum=track+1,
                track=track,
            )
        elif not state:
            sequencer.remove_note(
                position=i,
                track=track
            )

steps = menu.get_enum(TimerStep)

lcd_menu = Character_LCD_Menu(hardware.lcd, hardware.COLUMNS, hardware.ROWS, "Menu", (
    Percentage(
        title="Level",
        default=0.25,
        on_update=lambda value, item: menu.set_attribute(mixer.voice, 'level', value),
    ),
    Bool(
        title="Active",
        on_update=lambda value, item: menu.set_attribute(sequencer, 'active', value),
    ),
    pattern_group := Group("Pattern", tuple(
        [
            pattern_index := Number(
                title="Index",
                default=0,
                step=1,
                minimum=0,
                maximum=15,
                loop=True,
                decimals=0,
                on_update=lambda value, item: menu.load(pattern_group, item, value, 'pattern'),
            ),
            Action("Save", lambda: menu.save(pattern_group, pattern_index.value, 'pattern')),
            Number(
                title="BPM",
                step=1,
                default=120,
                minimum=60,
                maximum=240,
                decimals=0,
                on_update=lambda value, item: menu.set_attribute(sequencer, 'bpm', value),
            ),
            Number(
                title="Length",
                step=1,
                default=16,
                minimum=1,
                maximum=16,
                decimals=0,
                on_update=update_sequencer_length,
            ),
            List(
                title="Step",
                items=tuple([item[0] for item in steps]),
                on_update=lambda value, item: menu.set_attribute(sequencer, 'steps', steps[value][1]),
            ),
        ] + (sequence_items := [
            Sequence(
                title=voice.__class__.__name__,
                length = sequencer.length,
                on_update=lambda value, item, i=i: update_sequencer_track(i, value),
            )
            for i, voice in enumerate(voices)
        ])
    )),
    voice_group := Group("Voice", tuple(
        [
            voice_index := Number(
                title="Index",
                default=0,
                step=1,
                minimum=0,
                maximum=15,
                loop=True,
                decimals=0,
                on_update=lambda value, item: menu.load(voice_group, item, value, 'voice'),
            ),
            Action("Save", lambda: menu.save(voice_group, voice_index.value, 'voice')),
        ] + [
            Group(voice.__class__.__name__, (
                Mix(
                    title="Mix",
                    on_level_update=lambda value, item, voice=voice: menu.set_attribute(voice, 'amplitude', value),
                    on_pan_update=lambda value, item, voice=voice: menu.set_attribute(voice, 'pan', value),
                ),
                Number(
                    title="Tuning",
                    default=0,
                    step=1,
                    minimum=-12,
                    maximum=12,
                    show_sign=True,
                    decimals=0,
                    on_update=lambda value, item, voice=voice: menu.set_attribute(voice, 'tune', value),
                ),
                Group("Envelope", (
                    Percentage(
                        title="Attack Level",
                        default=1.0,
                        on_update=lambda value, item, voice=voice: menu.set_attribute(voice, 'attack_level', value),
                    ),
                    Percentage(
                        title="Decay Time",
                        step=0.05,
                        default=0.0,
                        minimum=-1.0,
                        maximum=1.0,
                        show_sign=True,
                        on_update=lambda value, item, voice=voice: menu.set_attribute(voice, 'decay_time', value)
                    ),
                )),
            ))
            for voice in voices
        ]
    )),
    Action("Exit", menu.load_launcher),
))

# Perform a full update which will synchronize sequencer and voice properties

lcd_menu.do_update()

## Controls

async def controls_task():
    while True:
        menu.handle_controls(lcd_menu)
        await asyncio.sleep(hardware.TASK_SLEEP)

## Asyncio loop

async def main():
    await asyncio.gather(
        asyncio.create_task(sequencer.update_async()),
        asyncio.create_task(touch_task()),
        asyncio.create_task(midi_task()),
        asyncio.create_task(controls_task()),
    )

asyncio.run(main())
