# =============================================================================
# On-Device Inference & Stream to Cloud
# APAN 5570 — WellSound Group Project
# UIFlow 2.x  |  M5Stack Core2  |  MicroPython  (no ulab, no array module)
# Author: Amy Moffatt
# =============================================================================
# SETUP
#   1. Run WellSound_ML.ipynb → produces weights.json
#   2. Upload to /flash/ on your Core2:
#        weights_2.json
#        /flash/certificate/device_cert.pem
#        /flash/certificate/private_key.pem
#        /flash/certificate/AmazonRootCA1.pem
#   3. Set ROOM_ID and ROOM_NAME below to match the physical device location
#   4. Copy-paste this script into UIFlow 2.x "Python" mode and Run
# =============================================================================

import M5
from M5 import *
import json, math, time, ustruct
import network, ssl, ntptime
from umqtt.simple import MQTTClient

# ── Startup ───────────────────────────────────────────────────────────────────
M5.begin()
M5.Lcd.clear()
M5.Lcd.setTextColor(0xFFFFFF, 0x000000)
M5.Lcd.setTextSize(1)
M5.Lcd.setCursor(10, 10)
M5.Lcd.print('WellSound\nLoading model...')

# =============================================================================
# 0. Device & cloud configuration
#    ↳ UPDATE these two values for each physical device before flashing
# =============================================================================
ROOM_ID   = '001'    #UDPATE room ID for your device
ROOM_NAME = 'Moffatt Library'  #UPDATE room name for your device

# ── WiFi ──────────────────────────────────────────────────────────────────────
WIFI_SSID     = 'Columbia University'
WIFI_PASSWORD = ''                  # Set your WiFi password here if required

# ── AWS IoT Core ──────────────────────────────────────────────────────────────
AWS_ENDPOINT  = 'YOUR_AWS_IOT_ENDPOINT'  # e.g. xxxx-ats.iot.us-east-1.amazonaws.com
CLIENT_ID     = 'wellsound-' + ROOM_ID
TOPIC         = 'wellsound/room-status/' + ROOM_ID

# Certificate paths — upload these files to /flash/certificate/ on the Core2
CERT_PATH = '/flash/certificate/device_cert.crt.crt'
KEY_PATH  = '/flash/certificate/Privatekey.key'
CA_PATH   = '/flash/certificate/AmazonRootCA1.pem'

#AWS_CA_CERT = '/flash/certificate/AmazonRootCA1.pem'
#AWS_CLIENT_CERT = '/flash/certificate/device_cert.crt.crt'
#AWS_PRIVATE_KEY = '/flash/certificate/Privatekey.key'

# ── Publish cadence ───────────────────────────────────────────────────────────
# 40 windows × 0.25 s = 10 s — publishes every 10 seconds
PUBLISH_EVERY = 40    # was 120

# =============================================================================
# 1. Load weights
# =============================================================================
with open('/flash/weights_2.json', 'r') as f:
    md = json.load(f)

W1      = md['coefs'][0]
W2      = md['coefs'][1]
W3      = md['coefs'][2]
b1      = md['intercepts'][0]
b2      = md['intercepts'][1]
b3      = md['intercepts'][2]
CLASSES = md['classes']
SC_MEAN = md['scaler_mean']
SC_STD  = md['scaler_std']

print('Model loaded. Classes:', CLASSES)

# =============================================================================
# 2. Constants  (must match WellSound_ML.ipynb exactly)
# =============================================================================
# BANDS must match WellSound_MLNotebook.ipynb exactly.
# compute_bandpower() uses these to get raw per-band energy.
# extract_features() then derives dB loudness + spectral ratios on top.
BANDS = [
    (0,    200),   # BP Sub-bass — HVAC rumble, deep bass thump
    (200,  500),   # BP Low      — low-mid, background presence
    (500,  2000),  # BP Mid      — core voice/speech range
    (2000, 5000),  # BP High     — harshness, speech intelligibility
    (5000, 8000),  # BP Air      — high-freq noise (chairs, printers)
]
SAMPLE_RATE = 16000
WIN_SAMPLES = 4000    # samples per window = 0.25 s at 16 kHz

# =============================================================================
# 3. WiFi, NTP & AWS IoT connectivity
# =============================================================================

def connect_wifi():
    M5.Lcd.setCursor(10, 10)
    M5.Lcd.print('WellSound\nConnecting WiFi...')
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        timeout = 20
        while not wlan.isconnected() and timeout > 0:
            time.sleep(1)
            timeout -= 1
    if not wlan.isconnected():
        raise RuntimeError('WiFi failed — check SSID/password')
    print('WiFi connected:', wlan.ifconfig()[0])


# Updated to avoid timeout
def sync_time():
    """Sync RTC via NTP — required for valid timestamps in payloads."""
    for attempt in range(3):
        try:
            ntptime.settime()
            print('NTP time synced:', time.localtime())
            return
        except Exception as e:
            print('[ntp] Attempt {} failed: {}'.format(attempt + 1, e))
            time.sleep(2)
    print('[ntp warning] Could not sync time — using device clock')


def connect_aws():
    M5.Lcd.setCursor(10, 10)
    M5.Lcd.print('WellSound\nConnecting AWS...')
    try:
        client = MQTTClient(
            client_id = CLIENT_ID,
            server    = AWS_ENDPOINT,
            port      = 8883,
            keepalive = 1200,
            ssl       = True,
            ssl_params = {
                'key':         KEY_PATH,
                'cert':        CERT_PATH,
                'server_side': False,
            }
        )
        client.connect()
        print('AWS IoT connected. Publishing to:', TOPIC)
        return client
    except Exception as e:
        print('[aws error]', e)
        M5.Lcd.clear()
        M5.Lcd.setCursor(10, 10)
        M5.Lcd.print('AWS FAILED:\n' + str(e))
        raise


def publish_prediction(client, label, conf, features):
    """
    Publish one room-status message to AWS IoT Core.
    Topic: wellsound/room-status/<room_id>
    Payload fields:
      room_id, room_name        — device identity
      aq_label, confidence      — smoothed ML output
      timestamp                 — Unix epoch (UTC, from NTP)
      rms_db, speech_ratio,     — all 6 features used for classification
      low_ratio, high_ratio,
      mid_ratio, centroid
    """
    payload = json.dumps({
        'room_id':      ROOM_ID,
        'room_name':    ROOM_NAME,
        'aq_label':     label,
        'confidence':   round(conf, 3),
        'timestamp':    time.time(),
        'rms_db':       round(features[0], 3),
        'speech_ratio': round(features[1], 4),
        'low_ratio':    round(features[2], 4),
        'high_ratio':   round(features[3], 4),
        'mid_ratio':    round(features[4], 4),
        'centroid':     round(features[5], 2),
        'model_version':'v1.0',
    })
    client.publish(TOPIC, payload.encode())
    print('[aws]', TOPIC, '->', label, round(conf, 3))

# =============================================================================
# 4. Pure-Python FFT  (N=256 sub-windows — matches WellSound_ML.ipynb)
# =============================================================================
_N      = 256    #updated from 1024 to increase inference speed
_N_SUB  = WIN_SAMPLES // _N    # 15 sub-windows per 0.25-s window
_HZ_BIN = SAMPLE_RATE / _N    # 62.5 Hz per bin

# Bit-reversal table (dynamic — works for any power-of-2 _N)
_n_bits = int(math.log2(_N))
_BR = [0] * _N
for _i in range(_N):
    _r, _x = 0, _i
    for _ in range(_n_bits):
        _r = (_r << 1) | (_x & 1)
        _x >>= 1
    _BR[_i] = _r

# Twiddle factors — computed once at startup
_WR = [math.cos(-2.0 * math.pi * k / _N) for k in range(_N)]
_WI = [math.sin(-2.0 * math.pi * k / _N) for k in range(_N)]

# Working buffers — reused each call to avoid heap allocation
_RE = [0.0] * _N
_IM = [0.0] * _N

# Band bin boundaries at 15.625 Hz/bin resolution
_BINS = []
for (f_lo, f_hi) in BANDS:
    _BINS.append((int(f_lo / _HZ_BIN), min(int(f_hi / _HZ_BIN), _N // 2 + 1)))


def _fft(samples, start):
    """Cooley-Tukey in-place FFT for N=256 real samples starting at 'start'."""
    RE, IM, BR, WR, WI, N = _RE, _IM, _BR, _WR, _WI, _N
    for i in range(N):
        RE[BR[i]] = float(samples[start + i])
        IM[BR[i]] = 0.0
    stage = 2
    while stage <= N:
        half   = stage >> 1
        t_step = N // stage
        for s in range(0, N, stage):
            for k in range(half):
                t  = k * t_step
                wr = WR[t]; wi = WI[t]
                i0 = s + k;  i1 = i0 + half
                tr = wr * RE[i1] - wi * IM[i1]
                ti = wr * IM[i1] + wi * RE[i1]
                RE[i1] = RE[i0] - tr
                IM[i1] = IM[i0] - ti
                RE[i0] += tr
                IM[i0] += ti
        stage <<= 1


def compute_bandpower(samples):
    """5-band bandpower via _N_SUB x N=256 sub-window FFTs (matches notebook)."""
    RE, IM, BINS, N, N_SUB = _RE, _IM, _BINS, _N, _N_SUB
    bp = [0.0, 0.0, 0.0, 0.0, 0.0]
    for w in range(N_SUB):
        _fft(samples, w * N)
        for i, (lo, hi) in enumerate(BINS):
            s = 0.0
            for k in range(lo, hi):
                r = RE[k]; im = IM[k]
                s += r*r + im*im
            bp[i] += s
    norm = 1.0 / (N_SUB * N)
    return [v * norm for v in bp]

# =============================================================================
# 5. NEW: Derived features — rms_db + spectral ratios
#    Must match extract_features_v2() in WellSound_MLNotebook.ipynb exactly.
#    Pipeline: samples → compute_bandpower() → ratios
#                      → rms_db()            → loudness
#                      → combined 6-feature list → predict()
# =============================================================================

def rms_db(samples):
    """RMS energy in dB relative to full int16 scale."""
    acc = 0.0
    for v in samples:
        f = v / 32768.0
        acc += f * f
    rms = math.sqrt(acc / len(samples) + 1e-10)
    return 20.0 * math.log10(rms)


def extract_features(samples):
    """
    Returns 6-element feature vector — must match training notebook exactly:
      [0] rms_db        — overall loudness (dB)
      [1] speech_ratio  — energy fraction in 500–2k Hz (core voice range)
      [2] low_ratio     — energy fraction in 0–500 Hz (rumble/bass combined)
      [3] high_ratio    — energy fraction in 2k–8k Hz (harshness combined)
      [4] mid_ratio     — energy fraction in 200–500 Hz (low-mid presence)
      [5] centroid      — spectral centre of mass (Hz)
    """
    bp                              = compute_bandpower(samples)
    sub_bass, low, mid, high, air   = bp
    eps                             = 1e-10
    full                            = sub_bass + low + mid + high + air + eps

    speech_ratio = mid              / full   # 500–2000 Hz — core voice
    low_ratio    = (sub_bass + low) / full   # 0–500 Hz   — rumble/bass
    high_ratio   = (high + air)     / full   # 2k–8k Hz   — harshness
    mid_ratio    = low              / full   # 200–500 Hz — low-mid presence
    centroid     = (sub_bass * 100.0 + low * 350.0 + mid * 1250.0 +
                    high * 3500.0 + air * 6500.0) / full

    return [rms_db(samples), speech_ratio, low_ratio, high_ratio, mid_ratio, centroid]

# =============================================================================
# 6. Inference  (scale now over 6 features, not 5)
# =============================================================================

def scale(x):
    # len(x) == 6, SC_MEAN/SC_STD exported from notebook with 6 entries
    return [(x[i] - SC_MEAN[i]) / SC_STD[i] for i in range(len(x))]

def dot(x, W, b):
    out = []
    for j in range(len(W[0])):
        s = b[j]
        for i in range(len(x)):
            s += x[i] * W[i][j]
        out.append(s)
    return out

def relu(x):
    return [max(0.0, v) for v in x]

def softmax(z):
    m = max(z)
    e = [math.exp(v - m) for v in z]
    s = sum(e)
    return [v / s for v in e]

def predict(features):
    x     = scale(features)
    h1    = relu(dot(x,  W1, b1))
    h2    = relu(dot(h1, W2, b2))
    probs = softmax(dot(h2, W3, b3))
    idx   = probs.index(max(probs))
    return CLASSES[idx], probs[idx]

# =============================================================================
# 7. Temporal smoothing — fixed 10-second rolling window majority vote
#    Buffer holds only the most recent 40 windows (10 seconds).
#    Fast to worsen (3 windows = 0.75s), slow to improve (10 windows = 2.5s)
# =============================================================================
QUALITY_RANK  = {'Focus': 0, 'Collaborative': 1, 'Lively': 2, 'Disruptive': 3}
SMOOTH_N_DOWN = 3     # windows to confirm worsening  — 3 × 0.25s = 0.75s
SMOOTH_N_UP   = 10    # windows to confirm improvement — 10 × 0.25s = 2.5s
SMOOTH_MAX    = 40    # maximum buffer size — 40 × 0.25s = 10 seconds
_label_buf    = []

def smooth_predict(new_label):
    """Rolling 10-second window — fast to worsen, slow to improve."""
    # Determine current label BEFORE appending new one
    current      = max(set(_label_buf), key=_label_buf.count) if _label_buf else new_label
    rank_new     = QUALITY_RANK.get(new_label, 1)
    rank_current = QUALITY_RANK.get(current, 1)
    limit        = SMOOTH_N_DOWN if rank_new > rank_current else SMOOTH_N_UP

    # Append new label and enforce both the speed limit and max window
    _label_buf.append(new_label)
    if len(_label_buf) > limit:
        _label_buf.pop(0)
    if len(_label_buf) > SMOOTH_MAX:
        _label_buf.pop(0)

    best, best_n = _label_buf[0], 0
    for candidate in set(_label_buf):
        n = _label_buf.count(candidate)
        if n > best_n:
            best, best_n = candidate, n
    return best

# =============================================================================
# 8. Display helper  (updated colours + subtitle for acoustic quality classes)
# =============================================================================
CLASS_COLORS = {
    'Focus':         0x4682B4,   # steelblue      — calm, quiet
    'Collaborative': 0x3CB371,   # mediumseagreen — active but healthy
    'Lively':        0xFFA500,   # orange          — high energy
    'Disruptive':    0xFF4500,   # orangered        — disruptive
}

CLASS_SUB = {
    'Focus':         'quiet & calm',
    'Collaborative': 'active voices',
    'Lively':        'high energy',
    'Disruptive':    'disruptive',
}

def show_result(label, conf, published=False):
    color = CLASS_COLORS.get(label, 0xFFFFFF)
    M5.Lcd.clear()
    M5.Lcd.setTextColor(color, 0x000000)
    M5.Lcd.setTextSize(3)
    M5.Lcd.setCursor(10, 30)
    M5.Lcd.print(label.upper())
    M5.Lcd.setTextColor(0xAAAAAA, 0x000000)
    M5.Lcd.setTextSize(1)
    M5.Lcd.setCursor(10, 80)
    M5.Lcd.print(CLASS_SUB.get(label, ''))
    M5.Lcd.setTextColor(0xFFFFFF, 0x000000)
    M5.Lcd.setTextSize(2)
    M5.Lcd.setCursor(10, 110)
    M5.Lcd.print(str(round(conf * 100)) + '%')
    M5.Lcd.setTextSize(1)
    M5.Lcd.setCursor(10, 140)
    M5.Lcd.print('confident')
    # Small cloud indicator bottom-right — green when just published
    M5.Lcd.setTextColor(0x00FF88 if published else 0x444444, 0x000000)
    M5.Lcd.setCursor(200, 220)
    M5.Lcd.print('AWS')

# =============================================================================
# 9. Main loop
# =============================================================================
_MIC_BUF = bytearray(WIN_SAMPLES * 2)   # int16 PCM buffer — 8 KB at 16 kHz

# ── Connect WiFi, sync clock, connect AWS ────────────────────────────────────
connect_wifi()
sync_time()
aws_client  = connect_aws()
_window_cnt = PUBLISH_EVERY        # counts inference windows, used for publish cadence
_published  = False    # controls the AWS indicator on screen

M5.Mic.begin()
show_result('Focus', 1.0)   # default display while room settles

while True:
    try:
        M5.Mic.record(_MIC_BUF, SAMPLE_RATE, False)
        while M5.Mic.isRecording():
            M5.update()
            time.sleep_ms(50)

        samples         = list(ustruct.unpack('<' + 'h' * WIN_SAMPLES, _MIC_BUF))
        features        = extract_features(samples)
        raw_label, conf = predict(features)
        label           = smooth_predict(raw_label)

        # ── Publish to AWS every PUBLISH_EVERY windows ───────────────────────
        _window_cnt += 1
        _published   = False
        if _window_cnt >= PUBLISH_EVERY:
            publish_prediction(aws_client, label, conf, features)
            _window_cnt = 0
            _published  = True

        show_result(label, conf, published=_published)

        print('[pred]', label, '(raw:', raw_label + ')', round(conf, 3),
              [round(v, 4) for v in features])

    except KeyboardInterrupt:
        print('Stopped.')
        aws_client.disconnect()
        break
    except Exception as e:
        # ── Reconnect on dropped connection ──────────────────────────────────
        print('[error]', e)
        M5.Lcd.setCursor(10, 10)
        M5.Lcd.print('Error - reconnecting...')
        time.sleep(5)
        try:
            aws_client = connect_aws()
        except Exception as e2:
            print('[reconnect failed]', e2)