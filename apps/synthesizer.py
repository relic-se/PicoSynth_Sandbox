# SPDX-FileCopyrightText: Copyright (c) 2024 Cooper Dalrymple
#
# SPDX-License-Identifier: Unlicense

import synthwaveform
import synthvoice.oscillator
import synthkeyboard

import audiomixer
import synthio

from relic_menumanager import Group, Number, String, Action, Percentage, List, Time
from relic_menumanager.synthio import Mix, Tune, Waveform, ADSREnvelope
from relic_menumanager.character_lcd import Character_LCD_Menu

import adafruit_midi
from adafruit_midi.note_on import NoteOn
from adafruit_midi.note_off import NoteOff
from adafruit_midi.control_change import ControlChange
from adafruit_midi.pitch_bend import PitchBend
from adafruit_midi.midi_message import MIDIMessage, MIDIUnknownEvent

from micropython import const

import asyncio
import board

import hardware
import menu
import settings

hardware.init()

try:
    import audiodelays
except ImportError:
    EFFECTS = 0
else:
    EFFECTS = 1
    EFFECTS_BUFFER = 2048
    DELAY_LENGTH = 250
    MAX_DELAY = DELAY_LENGTH * 4
    CHORUS_DELAY = 80

VOICES = 6 if board.board_id == "raspberry_pi_pico2" else 3
OSCILLATORS = 2 if board.board_id == "raspberry_pi_pico2" else 1

## Audio Output + Synthesizer

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

if EFFECTS:
    delay = audiodelays.Echo(
        max_delay_ms=DELAY_LENGTH,
        buffer_size=EFFECTS_BUFFER,
        channel_count=hardware.CHANNELS,
        sample_rate=hardware.SAMPLE_RATE,
    )

    chorus_lfo = synthio.LFO(scale=0.0, offset=0.0, rate=1.0)
    synth.blocks.append(chorus_lfo)
    def set_chorus_depth(value:float, item:Item = None) -> None:
        chorus_lfo.scale = CHORUS_DELAY * value / 2
        chorus_lfo.offset = CHORUS_DELAY * (1 - value / 2)

    chorus = audiodelays.Echo(
        max_delay_ms=CHORUS_DELAY,
        delay_ms=chorus_lfo,
        decay=0.0,
        mix=0.0,
        buffer_size=EFFECTS_BUFFER,
        channel_count=hardware.CHANNELS,
        sample_rate=hardware.SAMPLE_RATE,
    )

    chorus.play(synth)
    delay.play(chorus)
    mixer.voice[0].play(delay)

else:
    mixer.voice[0].play(synth)

oscillators = [synthvoice.oscillator.Oscillator(synth) for i in range(VOICES * OSCILLATORS)]

async def oscillator_task() -> None:
    while True:
        for i in range(len(oscillators)):
            oscillators[i].update()
        await asyncio.sleep(hardware.TASK_SLEEP)

## Keyboard Manager

keyboard = synthkeyboard.Keyboard(
    max_voices=VOICES,
    root=48,
)

class VoiceType:
    POLYPHONIC = const(0)
    MONOPHONIC = const(1)
    MONOPHONIC_ALL = const(2)
voice_types = menu.get_enum(VoiceType)
voice_type = VoiceType.POLYPHONIC

def set_voice_type(value:int, item:Item = None) -> None:
    global voice_type
    voice_type = value % len(voice_types)
    if voice_type == VoiceType.POLYPHONIC:
        keyboard.max_voices = VOICES
    else:
        keyboard.max_voices = 1

def voice_press(voice:synthvoice.Voice) -> None:
    global voice_type, oscillators
    start = 0
    stop = OSCILLATORS
    if voice_type == VoiceType.POLYPHONIC:
        start = voice.index * OSCILLATORS
        stop = (voice.index + 1) * OSCILLATORS
    elif voice_type == VoiceType.MONOPHONIC_ALL:
        stop = len(oscillators)
    for i in range(start, stop):
        oscillators[i].press(
            notenum=voice.note.notenum,
            velocity=voice.note.velocity,
        )
    hardware.led.value = True
keyboard.on_voice_press = voice_press

def voice_release(voice:synthvoice.Voice) -> None:
    global voice_type, oscillators, synth
    if (voice_type == VoiceType.MONOPHONIC or voice_type == VoiceType.MONOPHONIC_ALL) and not keyboard.notes:
        synth.release_all()
    elif voice_type == VoiceType.POLYPHONIC:
        for i in range(voice.index * OSCILLATORS, (voice.index + 1) * OSCILLATORS):
            oscillators[i].release()
    if not keyboard.notes:
        hardware.led.value = False
keyboard.on_voice_release = voice_release

keyboard.arpeggiator = synthkeyboard.Arpeggiator(
    steps=synthkeyboard.TimerStep.QUARTER,
    mode=synthkeyboard.ArpeggiatorMode.UP,
)

## USB & Hardware MIDI

def midi_process_message(msg:MIDIMessage) -> None:
    if settings.midi_thru and not isinstance(msg, MIDIUnknownEvent):
        hardware.midi_usb.send(msg)
        hardware.midi_uart.send(msg)

    if settings.midi_channel is not None and msg.channel != settings.midi_channel:
        return
    
    if isinstance(msg, NoteOn):
        if msg.velocity > 0.0:
            keyboard.append(msg.note, msg.velocity)
        else:
            keyboard.remove(msg.note)

    elif isinstance(msg, NoteOff):
        keyboard.remove(msg.note)

    elif isinstance(msg, ControlChange):
        if msg.control == 7: # Volume
            mixer.voice[0].level = msg.value / 127
        elif msg.control == 10: # Pan
            menu.set_attribute(oscillators, 'pan', msg.value / 64 - 1)
        elif msg.control == 11: # Expression
            menu.set_attribute(oscillators, 'velocity_amount', msg.value / 127)
        elif msg.control == 64: # Sustain
            keyboard.sustain = msg.value >= 64

    elif isinstance(msg, PitchBend):
        menu.set_attribute(oscillators, 'bend', (msg.pitch_bend - 8192) / 8192)
    
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

## Touch Keyboard Interface

def ttp_press(i:int) -> None:
    notenum = keyboard.root + i
    if settings.keyboard_touch:
        keyboard.append(notenum)
    if settings.midi_touch_out:
        msg = NoteOn(notenum)
        hardware.midi_uart.send(msg)
        hardware.midi_usb.send(msg)
hardware.ttp.on_press = ttp_press

def ttp_release(i:int) -> None:
    notenum = keyboard.root + i
    if settings.keyboard_touch:
        keyboard.remove(notenum)
    if settings.midi_touch_out:
        msg = NoteOff(notenum)
        hardware.midi_uart.send(msg)
        hardware.midi_usb.send(msg)
hardware.ttp.on_release = ttp_release

async def touch_task() -> None:
    while True:
        hardware.ttp.update()
        await asyncio.sleep(hardware.TASK_SLEEP)

## Character LCD Menu

def copy_oscillator_attrs(index:int = 0) -> None:
    if OSCILLATORS > 1:
        menu.write_message("Copying...")
        menu_index, menu_item = lcd_menu.find("Osc 1")
        if menu_index is None:
            menu.write_message("Failed to Copy!", True)
        else:
            data = lcd_menu[menu_index + index]
            for i in range(OSCILLATORS):
                if i == index:
                    continue
                lcd_menu[menu_index + i].data = data
            menu.write_message("Complete!")


lcd_menu = Character_LCD_Menu(hardware.lcd, hardware.COLUMNS, hardware.ROWS, "Menu", tuple(
    [
        Group("Patch", (
            patch := Number(
                title="Index",
                default=0,
                step=1,
                minimum=0,
                maximum=15,
                loop=True,
                decimals=0,
                on_update=lambda value, item: menu.load(lcd_menu, item, value, 'synthesizer'),
            ),
            String("Name"),
            Action("Save", lambda: menu.save(lcd_menu, patch.value, 'synthesizer')),
        )),
        Group("Audio", (
            Percentage(
                title="Level",
                default=1.0,
                on_update=lambda value, item: menu.set_attribute(mixer.voice, 'level', value),
            ),
        )),
        Group("Keys", (
            List(
                title="Priority",
                items=("High", "Low", "Last"),
                on_update=lambda value, item: menu.set_attribute(keyboard, 'mode', value),
            ),
            List(
                title="Voice",
                items=tuple([item[0] for item in voice_types]),
                on_update=set_voice_type,
            ),
            menu.get_arpeggiator_group(keyboard.arpeggiator),
        )),
    ] + [
        Group("Osc {:d}".format(i + 1) if OSCILLATORS > 1 else "Oscillator", (
            Mix(
                title="Mix",
                on_level_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'amplitude', value),
                on_pan_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'pan', value),
            ),
            Tune(
                title="Tuning",
                on_coarse_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'coarse_tune', value),
                on_fine_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'fine_tune', value),
                on_glide_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'glide', value),
                on_bend_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'bend_amount', value),
                on_slew_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'pitch_slew', value),
                on_slew_time_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'pitch_slew_time', value),
            ),
            Waveform(
                title="Waveform",
                items=(
                    ("Sine", synthwaveform.sine),
                    ("Saw", synthwaveform.saw),
                    ("Triangle", synthwaveform.triangle),
                    ("Square", synthwaveform.square),
                    ("Noise", synthwaveform.noise),
                ),
                on_waveform_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'waveform', item.data),
                on_loop_start_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'waveform_loop', (value, oscillators[i].waveform_loop[1])),
                on_loop_end_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'waveform_loop', (oscillators[i].waveform_loop[0], value)),
            ),
            ADSREnvelope(
                title="Envelope",
                on_attack_time_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'attack_time', value),
                on_attack_level_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'attack_level', value),
                on_decay_time_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'decay_time', value),
                on_sustain_level_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'sustain_level', value),
                on_release_time_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'release_time', value),
            ),
            Percentage(
                title="Velocity",
                on_update=lambda value, item: menu.set_attribute(oscillators[i::OSCILLATORS], 'velocity_amount', value),
            ),
            Group("Filter", (
                List(
                    title="Type",
                    items=("Low Pass", "High Pass", "Band Pass"),
                    on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'filter_type', value),
                ),
                Number(
                    title="Frequency",
                    default=1.0,
                    step=0.01,
                    minimum=0,
                    maximum=min(20000, hardware.SAMPLE_RATE / 2),
                    smoothing=3.0,
                    decimals=0,
                    append="hz",
                    on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'filter_frequency', value),
                ),
                Number(
                    title="Resonance",
                    default=0.0,
                    step=0.01,
                    minimum=0.7071067811865475,
                    maximum=2.0,
                    smoothing=2.0,
                    decimals=3,
                    on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'filter_resonance', value),
                ),
                Group("Envelope", (
                    Time(
                        title="Attack",
                        on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'filter_attack_time', value),
                    ),
                    Number(
                        title="Amount",
                        default=0,
                        step=10,
                        minimum=min(20000, hardware.SAMPLE_RATE / 2) / -2,
                        maximum=min(20000, hardware.SAMPLE_RATE / 2) / 2,
                        append="hz",
                        on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'filter_amount', value),
                    ),
                    Time(
                        title="Release",
                        on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'filter_release_time', value),
                    ),
                )),
                Group("LFO", (
                    Number(
                        "Depth",
                        default=0.0,
                        step=0.01,
                        minimum=0,
                        maximum=min(20000, hardware.SAMPLE_RATE / 2) / 2,
                        smoothing=3.0,
                        decimals=0,
                        append="hz",
                        on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'filter_depth', value),
                    ),
                    Number(
                        "Rate",
                        step=0.01,
                        maximum=32.0,
                        smoothing=2.0,
                        append="hz",
                        on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'filter_rate', value),
                    ),
                    Time(
                        "Delay",
                        step=0.01,
                        minimum=0.0,
                        maximum=10.0,
                        on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'filter_delay', value),
                    ),
                )),
            )),
            Group("Mod", (
                Group("Tremolo", (
                    Percentage(
                        title="Depth",
                        on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'tremolo_depth', value / 2),
                    ),
                    Number(
                        title="Rate",
                        step=0.01,
                        maximum=32.0,
                        smoothing=2.0,
                        append="hz",
                        on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'tremolo_rate', value),
                    ),
                    Time(
                        title="Delay",
                        step=0.01,
                        minimum=0.0,
                        maximum=10.0,
                        on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'tremolo_delay', value),
                    ),
                )),
                Group("Vibrato", (
                    Number(
                        title="Depth",
                        default=0,
                        step=10,
                        minimum=0,
                        maximum=600,
                        decimals=0,
                        append=" cents",
                        on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'vibrato_depth', value / 1200),
                    ),
                    Number(
                        title="Rate",
                        step=0.01,
                        maximum=32.0,
                        smoothing=2.0,
                        append="hz",
                        on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'vibrato_rate', value),
                    ),
                    Time(
                        title="Delay",
                        step=0.01,
                        minimum=0.0,
                        maximum=10.0,
                        on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'vibrato_delay', value),
                    ),
                )),
                Group("Pan", (
                    Percentage(
                        title="Depth",
                        on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'pan_depth', value),
                    ),
                    Number(
                        title="Rate",
                        step=0.01,
                        maximum=32.0,
                        smoothing=2.0,
                        append="hz",
                        on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'pan_rate', value),
                    ),
                    Time(
                        title="Delay",
                        step=0.01,
                        minimum=0.0,
                        maximum=10.0,
                        on_update=lambda value, item, i=i: menu.set_attribute(oscillators[i::OSCILLATORS], 'pan_delay', value),
                    ),
                )),
            )),
        )) for i in range(OSCILLATORS)
    ] + ([
        Group("Effects", (
            Group("Chorus", (
                Percentage(
                    title="Depth",
                    on_update=set_chorus_depth,
                ),
                Number(
                    title="Rate",
                    step=0.01,
                    maximum=4.0,
                    smoothing=2.0,
                    append="hz",
                    on_update=lambda value, item: menu.set_attribute(chorus_lfo, 'rate', value),
                ),
                Percentage(
                    title="Mix",
                    on_update=lambda value, item: menu.set_attribute(chorus, 'mix', value),
                ),
            )),
            Group("Delay", (
                Number(
                    title="Time",
                    step=0.01,
                    default=0.5,
                    minimum=10,
                    maximum=MAX_DELAY,
                    smoothing=2.0,
                    append="ms",
                    decimals=0,
                    on_update=lambda value, item: menu.set_attribute(delay, 'delay_ms', value),
                ),
                Percentage(
                    title="Feedback",
                    on_update=lambda value, item: menu.set_attribute(delay, 'decay', value),
                ),
                Percentage(
                    title="Mix",
                    on_update=lambda value, item: menu.set_attribute(delay, 'mix', value),
                ),
            )),
        )),
    ] if EFFECTS else []) + ([
        Group("Tools", tuple([
            Action("Copy Osc {:d}".format(i+1), lambda i=i: copy_oscillator_attrs(i)) for i in range(OSCILLATORS)
        ])),
    ] if OSCILLATORS > 1 else []) + [
        Action("Exit", menu.load_launcher),
    ]
))

# Perform a full update which will synchronize oscillator properties

lcd_menu.do_update()

## Controls

async def controls_task():
    while True:
        menu.handle_controls(lcd_menu)
        await asyncio.sleep(hardware.TASK_SLEEP)

## Asyncio loop

async def main():
    await asyncio.gather(
        asyncio.create_task(keyboard.arpeggiator.update()),
        asyncio.create_task(oscillator_task()),
        asyncio.create_task(touch_task()),
        asyncio.create_task(midi_task()),
        asyncio.create_task(controls_task()),
    )

asyncio.run(main())
