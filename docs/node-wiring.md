# Node Wiring: 2 × INMP441 → ESP32-S3-DevKitC-1 (N16R8), one breadboard

**Demo configuration (2026-10-21).** Each node is one breadboard with one ESP32-S3 and one
pair of mics. Every node is wired identically. Node identity lives in firmware config (NVS),
so adding a node means building another copy and nothing else.

Source: [datasheets/INMP441.pdf](datasheets/INMP441.pdf), DS-INMP441-00 Rev 1.1 (InvenSense,
2014). The TDK link now redirects, so this copy comes from the CERN course mirror.

---

## 1. INMP441 electrical summary (from datasheet)

| Parameter | Value | Notes |
|---|---|---|
| Supply VDD | **1.62–3.63 V** (specified 1.8–3.3 V) | **Use the ESP32's 3V3 pin. Never 5 V.** Abs max 3.63 V. |
| Supply current @ 3.3 V | 2.2 mA typ, 2.5 mA max | 2 mics ≈ 5 mA |
| Logic levels | VIH ≥ 0.7·VDD, VIL ≤ 0.25·VDD | 3.3 V ESP32 GPIO is a direct match, no level shifting |
| Digital input max | VDD + 0.3 V | Same 3V3 rail as the ESP32 |
| SCK frequency | 0.5–3.2 MHz, and **must be exactly 64 × WS** | 16 kHz → 1.024 MHz. 48 kHz → 3.072 MHz (both OK) |
| WS (sample rate) | 7.8–50 kHz | |
| Data format | I2S Philips, 24-bit two's complement, MSB-first, 1-SCK delay, 32-bit slots | Read 32-bit slots, arithmetic shift right by 8 |
| L/R pin | Low → left slot, high → right slot | **Must be tied. Don't leave it floating.** |
| SD pin | Tri-state outside its slot, needs 100 kΩ pull-down | **Fitted on the Shutao modules (measured 100 kΩ).** Two on one line ≈ 50 kΩ, fine. |
| Decoupling | 0.1 µF X7R at VDD | On the breakout |
| Startup | Zero output for 2¹⁸ SCK cycles (256 ms at 1.024 MHz) | Firmware discards the first ~300 ms |
| Filter group delay | 17.2 / fs (1.075 ms at 16 kHz) | Same on both mics, so it cancels in the pair delay |
| Passband | 0.423·fs (6.77 kHz at 16 kHz) | Covers the 1.5–3 kHz band ✓ |
| Phase matching | **Not specified** | As the proposal states. Measure it in calibration. |
| Don't | Clock the mics while VDD is off | Powering the mics from the ESP32's 3V3 avoids this |

---

## 2. Breakout pins (round Shutao module)

`SD · VDD · GND · L/R · WS · SCK`. CHIPEN is tied high on the module.
**Bottom-port:** the sound hole goes through the PCB. Mount with the hole facing **up**,
away from the breadboard.

---

## 3. Pin assignment: one I2S controller, 6 wires

| ESP32-S3 (DevKitC-1 J1 pin) | Signal | Connects to |
|---|---|---|
| 3V3 (J1-1) | Mic power + logic high | VDD on M1 and M2, **L/R on M2** |
| GND (J1-22) | Ground | GND on M1 and M2, **L/R on M1** |
| GPIO4 (J1-4) | I2S0 SCK out | SCK on M1 and M2 |
| GPIO5 (J1-5) | I2S0 WS out | WS on M1 and M2 |
| GPIO6 (J1-6) | I2S0 DIN | SD on M1 and M2 |

| Mic | L/R | Channel |
|---|---|---|
| M1 | GND | ch0 (left slot) |
| M2 | 3V3 | ch1 (right slot) |

Both mics are on one data line clocked by one controller, so they are sample-aligned in
hardware (datasheet: "synchronized by the WS signal"). No jumpers and no cross-controller risk.

Pins avoided on the N16R8: 0, 3, 45, 46 (strapping), 19/20 (USB), 26–32 (flash),
33–37 (octal PSRAM), 43/44 (UART0 console), and 38/48 (RGB LED). Check the GPIO numbers on
your board's silkscreen. Clone boards sometimes reorder header positions.

---

## 4. Breadboard layout (standard 830-point, 63 rows)

```
 rows  1–6    M1   ← mic header down one column, one pin per row, hole facing up
 rows 21–26   M2   ← 20 rows (50.8 mm) from M1 → unambiguous to 3.38 kHz
 rows 30–55   ESP32-S3 DevKitC-1 straddling the trench (USB end facing away from the mics)
```

- Mark the **test side** of the board with an arrow, perpendicular to the M1–M2 line. The
  node reports the bearing 0–180° on that side: 0° = off the M1 end, 90° = straight out
  the arrow, 180° = off the M2 end.
- Keep the phone roughly within 30–150°. Accuracy falls off toward the ends of the line.
- Each of a mic's 6 pins must be in a **different numbered row**. Use the free holes in those
  rows for the jumper wires.
- Measure the actual hole-to-hole distance between the mics to ±0.5 mm and record it in the
  node config. 1 mm error on 50 mm is a 2% delay error.
- Keep SCK/WS wires short and tidy. A GND wire run alongside helps.

---

## 5. Power: from the Pi's USB ports

- Each ESP32 connects with one USB cable to the Pi 5, into the DevKit port labelled **USB** (the S3's
  native USB). Not the COM/UART port: its CH340K bridge drops bytes on long transfers (decision D21).
- **Use the official Pi 5 27 W (5 V / 5 A) supply.** With a lesser supply the Pi 5 limits
  *total* USB output to 600 mA, and two ESP32s with Wi-Fi peaks (~350 mA each) can brown out.
  With the 27 W supply the limit is 1.6 A, which is plenty. (With no Wi-Fi in use, a node draws
  well under 150 mA.)
- One power source per ESP32: the USB cable only. Nothing on the 5V or 3V3 pins.
- Mic power always comes from the DevKit's 3V3 pin.

---

## 6. First bring-up checklist

- [ ] Logic analyser (optional): SCK = 64 × WS, WS = sample rate
- [ ] Both channels non-zero after ~300 ms, with similar RMS in a quiet room
- [ ] Tap M1, then M2: only that channel spikes (confirms L/R wiring)
- [ ] Clap straight out from the arrow (90°): the two channels align within a fraction of a sample
- [ ] Clap off the M1 end: M1 leads M2 by ~148 µs (2.4 samples at 16 kHz)
