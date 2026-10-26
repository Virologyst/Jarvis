// Node <-> host serial protocol. Mirrored in host/nodelink/protocol.py; keep the two in step.
//
// Node -> host: binary frames
//   0xA5 0x5A | type u8 | len u16 LE | payload[len] | crc16 u16 LE
//   crc16 = CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over type, len and payload.
//   The host resyncs on the magic bytes, so stray boot text on the line is skipped.
//
// Host -> node: ASCII command lines terminated by '\n' (see main.c handle_command).
#pragma once

#include <stdint.h>

#define PROTO_MAGIC0   0xA5
#define PROTO_MAGIC1   0x5A
#define PROTO_VERSION  1

enum {
    MSG_HEARTBEAT = 0x01,
    MSG_BEARING   = 0x02,
    MSG_AUDIO     = 0x03,
    MSG_LOG       = 0x04,
};

enum {
    MODE_OFF     = 0,
    MODE_BEARING = 1 << 0,
    MODE_RAW     = 1 << 1,
    MODE_BOTH    = MODE_BEARING | MODE_RAW,
};

#define BEARING_FLAG_DETECTED  (1 << 0)  // gate passed; theta is meaningful
#define BEARING_FLAG_LINEAR    (1 << 1)  // linear pair: theta is from the array axis, elev unused

typedef struct __attribute__((packed)) {
    uint8_t  proto_version;
    uint8_t  node_id;
    uint8_t  mode;
    uint8_t  mic_count;
    uint32_t uptime_ms;
    uint32_t sample_rate;
    float    spacing_m;
    float    rms_dbfs[2];    // full-band level per channel over the last hop
    float    peak_dbfs[2];   // peak per channel since the previous heartbeat
    uint32_t i2s_overruns;
    uint32_t tx_dropped;
    char     fw_version[16];
} msg_heartbeat_t;

typedef struct __attribute__((packed)) {
    uint8_t  node_id;
    uint8_t  flags;
    uint16_t reserved;
    uint32_t seq;
    uint32_t t_ms;           // node uptime at the end of the analysis frame
    float    theta_deg;      // 0 = off the M1 end, 90 = broadside, 180 = off the M2 end
    float    elev_deg;       // NaN for a linear pair
    float    coherence;      // peak PHAT-SRP / bins, 0..1
    float    level_dbfs;     // in-band level
    float    tau_us;         // M2 delay relative to M1 (positive = M1 hears it first)
} msg_bearing_t;

typedef struct __attribute__((packed)) {
    uint8_t  node_id;
    uint8_t  channels;
    uint16_t n_frames;       // samples per channel in this message
    uint32_t seq;
    uint32_t first_sample;   // running sample index of the first frame
    // followed by 24-bit signed samples, 3 bytes LE each, interleaved [M1, M2, M1, M2, ...]
} msg_audio_hdr_t;
