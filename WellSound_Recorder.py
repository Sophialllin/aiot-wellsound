# ============================================================
# WellSound – Acoustic Quality Data Recorder
# Based on APAN 5570 Module 10 Sound Recorder
# ============================================================
# Records FOUR 3-minute audio clips — one per acoustic class:
#
#   1. "focus"       – very quiet room, minimal sound
#   2. "collab"      – moderate conversation, calm activity
#   3. "lively"      – busy café, active group work
#   4. "disruptive"  – loud / harsh noise, construction, chaos
#
# Saves to:
#   /flash/focus.wav       (~5.7 MB, 3 min)
#   /flash/collab.wav      (~5.7 MB, 3 min)
#   /flash/lively.wav      (~5.7 MB, 3 min)
#   /flash/disruptive.wav  (~5.7 MB, 3 min)
#
# ⚠️  IMPORTANT — parameters MUST match ML training notebook:
#     SAMPLE_RATE     = 16000 Hz
#     BITS_PER_SAMPLE = 16
#     NUM_CHANNELS    = 1 (mono)
#
# HOW TO RUN:
#   1. Paste into UIFlow 2.x (Python mode) and click Run
#   2. Follow on-screen prompts; press BtnA to start each class
#   3. After "ALL DONE" download the four .wav files from
#      /flash/ via the UIFlow file manager (folder icon)
#   4. Send files to YooJin for ML model training
#
# RECORDING TIPS for each class:
#   focus      — record in a quiet library or empty room.
#                Stay still, minimal movement.
#   collab     — record in a study room with 2-3 people talking
#                calmly, no loud music or background noise.
#   lively     — record in a busy café, cafeteria, or hallway
#                with active conversations and movement.
#   disruptive — record near construction, loud music, crowd
#                chaos, or create harsh/loud sounds near the mic.
#
# RECORDING VARIETY (important for model generalization):
#   - Try to record in at least 2-3 different real locations
#     per class if possible (run script multiple times)
#   - Aim for 3-5 min per class minimum
#   - Keep recording continuous — do NOT stitch clips together
# ============================================================

import M5
from M5 import *
import time, struct, gc

# ── Configuration ──────────────────────────────────────────────────────────────
SAMPLE_RATE      = 16000  # Hz
BITS_PER_SAMPLE  = 16
NUM_CHANNELS     = 1
BYTES_PER_SAMPLE = BITS_PER_SAMPLE // 8

# 3 minutes recorded as 6 × 30-second chunks
# Each chunk = 16000 * 30 * 2 = 960,000 bytes (~960 KB) — safe on flash
# Reduce CHUNK_SEC or NUM_CHUNKS if you hit memory errors
CHUNK_SEC  = 30
NUM_CHUNKS = 6                                         # 6 × 30s = 3 minutes total
TOTAL_SEC  = CHUNK_SEC * NUM_CHUNKS                    # 180 seconds

CHUNK_BYTES = SAMPLE_RATE * CHUNK_SEC * BYTES_PER_SAMPLE   # 960,000 bytes
TOTAL_BYTES = CHUNK_BYTES * NUM_CHUNKS                     # 5,760,000 bytes

# ── Acoustic class definitions ─────────────────────────────────────────────────
# (label, filename, prep instruction, in-recording reminder, display colour)
CLASSES = [
    (
        "FOCUS",
        "focus.wav",
        "Find a QUIET room.\nMinimal talking or\nmovement.",
        "Stay quiet...",
        0x00CCFF,   # light blue
    ),
    (
        "COLLAB",
        "collab.wav",
        "Record calm\nconversation / light\nactivity nearby.",
        "Keep it calm...",
        0x00CC66,   # green
    ),
    (
        "LIVELY",
        "lively.wav",
        "Record in a busy\ncafe / cafeteria\nor active hallway.",
        "Busy noise...",
        0xFF8800,   # amber
    ),
    (
        "DISRUPTIVE",
        "disruptive.wav",
        "Record harsh noise:\nloud music, crowd\nor construction.",
        "Loud / harsh...",
        0xFF2200,   # red
    ),
]

# ── WAV header (44 bytes, standard PCM) ────────────────────────────────────────
def make_wav_header(sample_rate, bits_per_sample, num_channels, data_size):
    byte_rate   = sample_rate * num_channels * (bits_per_sample // 8)
    block_align = num_channels * (bits_per_sample // 8)
    h  = struct.pack('<4sI4s',  b'RIFF', 36 + data_size, b'WAVE')
    h += struct.pack('<4sIHHIIHH',
                     b'fmt ', 16, 1, num_channels, sample_rate,
                     byte_rate, block_align, bits_per_sample)
    h += struct.pack('<4sI', b'data', data_size)
    return h

# ── Core2 initialisation ───────────────────────────────────────────────────────
M5.begin()
Widgets.fillScreen(0x000000)

# Speaker and Mic share I2S — disable speaker first
Speaker.begin()
Speaker.setVolumePercentage(0)
Speaker.end()

# ── Display helpers ────────────────────────────────────────────────────────────
def show(line1, line2="", line3="", color=0xFFFFFF, bg=0x000000):
    Widgets.fillScreen(bg)
    if line1:
        Widgets.Label(line1, 20,  40, 1.0, color,    bg, Widgets.FONTS.DejaVu24)
    if line2:
        Widgets.Label(line2, 20, 100, 1.0, 0xFFFFFF, bg, Widgets.FONTS.DejaVu18)
    if line3:
        Widgets.Label(line3, 20, 155, 1.0, 0xAAAAAA, bg, Widgets.FONTS.DejaVu18)
    M5.update()

def update_countdown(text, bg=0xFF0000):
    """Overwrite only the bottom line — avoids full-screen flicker."""
    Widgets.Label(f"  {text}  ", 20, 155, 1.0, 0xFFFFFF, bg, Widgets.FONTS.DejaVu18)
    M5.update()

# ── Silent-chunk detector ──────────────────────────────────────────────────────
def peak_amplitude(buf, n_samples=200):
    """Return max absolute int16 value in first n_samples. 0 = recording failed."""
    peak = 0
    for i in range(0, min(n_samples * 2, len(buf) - 1), 2):
        v = struct.unpack_from('<h', buf, i)[0]
        if v < 0:
            v = -v
        if v > peak:
            peak = v
    return peak

# ── Main recorder ──────────────────────────────────────────────────────────────
def record_and_save(filename, class_label, instruction, reminder, class_color):

    # 1. Show prep screen — wait for BtnA
    show(f"NEXT: {class_label}", instruction, "Press BtnA to start", class_color)
    while True:
        M5.update()
        if BtnA.wasPressed():
            break
        time.sleep_ms(50)

    # 2. Countdown
    for i in range(3, 0, -1):
        show(class_label, f"Starting in {i}...", "", class_color)
        time.sleep(1)

    # 3. Write WAV header upfront (full data size already known)
    filepath = '/flash/' + filename
    with open(filepath, 'wb') as f:
        f.write(make_wav_header(SAMPLE_RATE, BITS_PER_SAMPLE, NUM_CHANNELS, TOTAL_BYTES))

    # 4. Pre-allocate buffer ONCE — reused across all chunks
    gc.collect()
    buf = bytearray(CHUNK_BYTES)

    # 5. Start microphone ONCE before chunk loop
    Mic.begin()
    time.sleep_ms(200)   # let I2S driver stabilise

    t_total = time.ticks_ms()

    for chunk in range(NUM_CHUNKS):
        chunk_offset_sec = chunk * CHUNK_SEC
        secs_remaining   = TOTAL_SEC - chunk_offset_sec

        # Show chunk header (full refresh once per chunk only)
        show(
            "RECORDING",
            f"{class_label}  ({chunk + 1}/{NUM_CHUNKS})",
            f"{reminder}  {secs_remaining}s left",
            0xFF0000
        )

        t_chunk = time.ticks_ms()
        Mic.record(buf, SAMPLE_RATE, False)   # False = mono

        # Wait; update countdown every second
        last_tick = -1
        while Mic.isRecording():
            M5.update()
            time.sleep_ms(200)
            elapsed = time.ticks_diff(time.ticks_ms(), t_chunk) // 1000
            if elapsed != last_tick:
                secs_left = max(0, TOTAL_SEC - chunk_offset_sec - elapsed)
                mins = secs_left // 60
                secs = secs_left % 60
                update_countdown(f"{reminder}  {mins}m {secs:02d}s left")
                last_tick = elapsed

        # 6. Silent-chunk check
        peak = peak_amplitude(buf)
        if peak == 0:
            show("WARNING!", f"Chunk {chunk + 1} is SILENT",
                 "Re-run this class!", 0xFF8800)
            time.sleep(4)

        # 7. Append raw PCM to file
        with open(filepath, 'ab') as f:
            f.write(buf)

        gc.collect()

    # 8. Shut down mic after all chunks done
    Mic.end()

    actual_s         = time.ticks_diff(time.ticks_ms(), t_total) / 1000.0
    total_file_bytes = 44 + TOTAL_BYTES
    show(
        "Saved!",
        filename,
        f"{total_file_bytes // 1024} KB  ({actual_s:.0f}s recorded)",
        0x00FF00
    )
    time.sleep(3)
    return total_file_bytes

# ── Intro screen ───────────────────────────────────────────────────────────────
show(
    "WellSound",
    "Acoustic Recorder",
    f"4 classes  x  {TOTAL_SEC}s each",
    0xFFFFFF
)
time.sleep(3)

show(
    "TIPS",
    "Record in real rooms!\nVariety = better model",
    "Press BtnA to continue",
    0xFFFF00
)
while True:
    M5.update()
    if BtnA.wasPressed():
        break
    time.sleep_ms(50)

# ── Record all four classes ────────────────────────────────────────────────────
results = []
for label, fname, instr, reminder, color in CLASSES:
    nbytes = record_and_save(fname, label, instr, reminder, color)
    results.append((label, fname, nbytes))
    time.sleep(1)

# ── Summary screen ─────────────────────────────────────────────────────────────
Widgets.fillScreen(0x000000)
Widgets.Label("ALL DONE!", 20, 10, 1.0, 0x00FF00, 0x000000, Widgets.FONTS.DejaVu24)

y_pos = 55
for label, fname, nbytes in results:
    kb = nbytes // 1024
    Widgets.Label(f"{fname}: {kb} KB", 20, y_pos, 1.0,
                  0xFFFFFF, 0x000000, Widgets.FONTS.DejaVu18)
    y_pos += 32

Widgets.Label("Send .wav files to YooJin", 20, y_pos + 10, 1.0,
              0xFFFF00, 0x000000, Widgets.FONTS.DejaVu18)
Widgets.Label("Download via UIFlow Files", 20, y_pos + 42, 1.0,
              0xAAAAAA, 0x000000, Widgets.FONTS.DejaVu18)

while True:
    M5.update()
    time.sleep(0.1)
