# SPDX-FileCopyrightText: Copyright (c) 2024 Cooper Dalrymple
#
# SPDX-License-Identifier: Unlicense

# Demonstration of dedicated voice without presets

from relic_keymanager import Keyboard
from relic_synthvoice import Voice
from relic_synthvoice.oscillator import Oscillator
import relic_waveform

import audiomixer
import synthio

from relic_menumanager import Percentage, Action
from relic_menumanager.character_lcd import Character_LCD_Menu

import adafruit_midi
from adafruit_midi.note_on import NoteOn
from adafruit_midi.note_off import NoteOff
from adafruit_midi.control_change import ControlChange
from adafruit_midi.pitch_bend import PitchBend
from adafruit_midi.midi_message import MIDIMessage, MIDIUnknownEvent

import asyncio
import board

import hardware
import menu
import settings

hardware.init()

VOICES = 6 if hardware.is_rp2350() else 4

if hardware.is_rp2350():
    try:
        import audiodelays
    except ImportError:
        EFFECTS = 0
    else:
        EFFECTS = 1
        EFFECTS_BUFFER = 1024
        DELAY_LENGTH = 250
        CHORUS_DELAY = 50
else:
    EFFECTS = 0

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
        delay_ms=DELAY_LENGTH,
        decay=0.5,
        mix=0.5,
        buffer_size=EFFECTS_BUFFER,
        channel_count=hardware.CHANNELS,
        sample_rate=hardware.SAMPLE_RATE,
    )

    chorus_lfo = synthio.LFO(scale=0.0, offset=0.0, rate=1.0)
    synth.blocks.append(chorus_lfo)
    def set_chorus_depth(value:float) -> None:
        chorus_lfo.scale = CHORUS_DELAY * value / 2
        chorus_lfo.offset = CHORUS_DELAY * (1 - value / 2)
    set_chorus_depth(0.25)

    chorus = audiodelays.Echo(
        max_delay_ms=CHORUS_DELAY,
        delay_ms=chorus_lfo,
        decay=0.0,
        mix=0.5,
        buffer_size=EFFECTS_BUFFER,
        channel_count=hardware.CHANNELS,
        sample_rate=hardware.SAMPLE_RATE,
    )

    chorus.play(synth)
    delay.play(chorus)
    mixer.voice[0].play(delay)

else:
    mixer.voice[0].play(synth)

voices = [Oscillator(synth) for i in range(VOICES)]

waveform = relic_waveform.mix(
    relic_waveform.saw(),
    (relic_waveform.saw(frequency=2.0), 0.5),
    (relic_waveform.saw(frequency=3.0), 0.25),
    (relic_waveform.saw(frequency=4.0), 0.125)
)
envelope = synthio.Envelope(attack_time=0.02, attack_level=1.0, decay_time=0.05, sustain_level=0.5, release_time=0.25)
for voice in voices:
    voice.waveform = waveform
    voice.amplitude = 0.5
    voice.envelope = envelope
    voice.coarse_tune = -1

async def synth_task() -> None:
    while True:
        for voice in voices:
            voice.update()
        await asyncio.sleep(hardware.TASK_SLEEP)

## Keyboard Manager

keyboard = Keyboard(
    max_voices=VOICES,
    root=48,
)

def voice_press(voice:Voice) -> None:
    voices[voice.index].press(
        notenum=voice.note.notenum,
        velocity=voice.note.velocity,
    )
    hardware.led.value = True
keyboard.on_voice_press = voice_press

def voice_release(voice:Voice) -> None:
    voices[voice.index].release()
    if not keyboard.notes:
        hardware.led.value = False
keyboard.on_voice_release = voice_release

## USB & Hardware MIDI

def midi_process_message(msg:MIDIMessage) -> None:
    if settings.midi_thru and not isinstance(msg, MIDIUnknownEvent):
        hardware.midi_usb.send(msg, channel=msg.midi_channel)
        hardware.midi_uart.send(msg, channel=msg.midi_channel)
        
    if settings.midi_channel is not None and 1 <= settings.midi_channel <= 16 and msg.channel != settings.midi_channel - 1:
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
            menu.set_attribute(voices, 'pan', msg.value / 64 - 1)
        elif msg.control == 11: # Expression
            menu.set_attribute(voices, 'velocity_amount', msg.value / 127)
        elif msg.control == 64: # Sustain
            keyboard.sustain = msg.value >= 64

    elif isinstance(msg, PitchBend):
        menu.set_attribute(voices, 'bend', (msg.pitch_bend - 8192) / 8192)
    
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
        msg = NoteOn(notenum, channel=settings.midi_channel)
        hardware.midi_uart.send(msg, channel=msg.midi_channel)
        hardware.midi_usb.send(msg, channel=msg.midi_channel)
hardware.ttp.on_press = ttp_press

def ttp_release(i:int) -> None:
    notenum = keyboard.root + i
    if settings.keyboard_touch:
        keyboard.remove(notenum)
    if settings.midi_touch_out:
        msg = NoteOff(notenum, channel=settings.midi_channel)
        hardware.midi_uart.send(msg, channel=msg.midi_channel)
        hardware.midi_usb.send(msg, channel=msg.midi_channel)
hardware.ttp.on_release = ttp_release

async def touch_task() -> None:
    while True:
        hardware.ttp.update()
        await asyncio.sleep(hardware.TASK_SLEEP)

## Character LCD Menu

lcd_menu = Character_LCD_Menu(hardware.lcd, hardware.COLUMNS, hardware.ROWS, "Menu", (
    Percentage(
        title="Volume",
        default=1.0,
        on_update=lambda value, item: menu.set_attribute(mixer.voice, 'level', value),
    ),
    Action("Exit", menu.load_launcher)
))

## Controls

async def controls_task():
    while True:
        menu.handle_controls(lcd_menu)
        await asyncio.sleep(hardware.TASK_SLEEP)

## Asyncio loop

async def main():
    await asyncio.gather(
        asyncio.create_task(synth_task()),
        asyncio.create_task(touch_task()),
        asyncio.create_task(midi_task()),
        asyncio.create_task(controls_task()),
    )

asyncio.run(main())
