/* SPDX-License-Identifier: GPL-2.0-or-later
 * Read-only OBS 32.2.2 Windows x64 packet timing probe.
 * ABI declarations below follow obsproject/obs-studio tag 32.2.2:
 * libobs/obs-encoder.h, obs.h, obs-module.h; frontend/api/obs-frontend-api.h.
 * No private object layouts are accessed. Exact runtime version is required.
 * This module is inert unless our dedicated worker supplies the output path.
 */
#include <windows.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct encoder_packet_time { int64_t pts; uint64_t cts, fer, ferc, pir; };
struct encoder_packet {
    uint8_t *data; size_t size; int64_t pts, dts;
    int32_t timebase_num, timebase_den; int type; bool keyframe;
    int64_t dts_usec, sys_dts_usec; int priority, drop_priority;
    size_t track_idx; void *encoder;
};
_Static_assert(sizeof(void *) == 8, "Windows x64 required");
_Static_assert(offsetof(struct encoder_packet, sys_dts_usec) == 56, "OBS packet ABI");
_Static_assert(sizeof(struct encoder_packet) == 88, "OBS packet ABI");
_Static_assert(sizeof(struct encoder_packet_time) == 40, "OBS timing ABI");

typedef void (*packet_cb)(void *, struct encoder_packet *, struct encoder_packet_time *, void *);
typedef void (*event_cb)(int, void *);
static void (*add_packet)(void *, packet_cb, void *);
static void (*remove_packet)(void *, packet_cb, void *);
static void (*release_output)(void *);
static void *(*get_output)(void);
static void (*add_event)(event_cb, void *);
static void (*remove_event)(event_cb, void *);
static const char *(*get_version)(void);

/* Bounded 2-hour buffer at 30 FPS. No allocation or disk I/O in packet callback. */
#define CAPACITY 216000
struct row { int64_t pts, dts, timing_pts; uint64_t cts, fer, ferc, pir; int32_t num, den; bool key; };
static struct row *rows;
static size_t count;
static bool overflow, missing, paused, started, finished, registered;
static void *output;
static wchar_t path[32768];

static bool write_bytes(HANDLE file, const char *text) {
    DWORD written = 0, length = (DWORD)strlen(text);
    return WriteFile(file, text, length, &written, NULL) && written == length;
}

static void receive_packet(void *out, struct encoder_packet *pkt,
                           struct encoder_packet_time *timing, void *unused) {
    (void)out; (void)unused;
    if (!pkt || pkt->type != 1) return; /* OBS_ENCODER_VIDEO */
    if (pkt->track_idx != 0) { missing = true; return; }
    if (count == CAPACITY) { overflow = true; return; }
    struct row *r = &rows[count++];
    r->pts = pkt->pts; r->dts = pkt->dts;
    r->num = pkt->timebase_num; r->den = pkt->timebase_den;
    r->key = pkt->keyframe;
    r->cts = timing ? timing->cts : 0;
    r->fer = timing ? timing->fer : 0;
    r->ferc = timing ? timing->ferc : 0;
    r->pir = timing ? timing->pir : 0;
    r->timing_pts = timing ? timing->pts : 0;
    if (!timing || !timing->cts) missing = true;
}

static void finish(void) {
    if (!output) return;
    /* Removal synchronizes with OBS's packet callback mutex before buffer read. */
    remove_packet(output, receive_packet, NULL);
    release_output(output);
    output = NULL;
    HANDLE file = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_NEW,
                              FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return;
    bool ok = write_bytes(file, "{\"type\":\"header\",\"schema\":\"trace2task.obs-frame-clock.v1\",\"obs_version\":\"32.2.2\",\"clock\":\"windows_qpc_ns\",\"timestamp_kind\":\"composition\"}\n");
    char line[512];
    for (size_t i = 0; ok && i < count; ++i) {
        struct row *r = &rows[i];
        snprintf(line, sizeof(line), "{\"type\":\"frame\",\"pts\":%lld,\"dts\":%lld,\"timebase_num\":%d,\"timebase_den\":%d,\"cts_ns\":%llu,\"fer_ns\":%llu,\"ferc_ns\":%llu,\"pir_ns\":%llu,\"timing_pts\":%lld,\"keyframe\":%s}\n",
                 (long long)r->pts, (long long)r->dts, r->num, r->den,
                 (unsigned long long)r->cts, (unsigned long long)r->fer,
                 (unsigned long long)r->ferc, (unsigned long long)r->pir,
                 (long long)r->timing_pts, r->key ? "true" : "false");
        ok = write_bytes(file, line);
    }
    if (ok) {
        snprintf(line, sizeof(line), "{\"type\":\"footer\",\"count\":%llu,\"overflow\":%s,\"missing_timing\":%s,\"paused\":%s,\"status\":\"%s\"}\n",
                 (unsigned long long)count, overflow ? "true" : "false", missing ? "true" : "false",
                 paused ? "true" : "false", count && !overflow && !missing && !paused ? "complete" : "invalid");
        write_bytes(file, line);
    }
    FlushFileBuffers(file);
    CloseHandle(file);
    finished = true;
}

static void frontend_event(int event, void *unused) {
    (void)unused;
    if (event == 4 && !started) { /* RECORDING_STARTING, before first output packet */
        started = true;
        output = get_output();
        if (output) add_packet(output, receive_packet, NULL);
    } else if (event == 7) { /* RECORDING_STOPPED */
        finish();
    } else if (event == 27 || event == 28) { /* PAUSED / UNPAUSED */
        paused = true;
    }
}

#define API __declspec(dllexport)
API void obs_module_set_pointer(void *module) { (void)module; }
API uint32_t obs_module_ver(void) { return (32U << 24) | (2U << 16) | 2U; }
API const char *obs_module_name(void) { return "Trace2Task frame clock"; }
API const char *obs_module_description(void) { return "Read-only PTS/QPC composition clock sidecar"; }
API bool obs_module_load(void) {
    DWORD length = GetEnvironmentVariableW(L"TRACE2TASK_OBS_FRAME_CLOCK", path, 32768);
    if (!length) return true; /* Not a Trace2Task capture. */
    if (length >= 32760) return false;
    HMODULE obs = GetModuleHandleW(L"obs.dll");
    HMODULE frontend = GetModuleHandleW(L"obs-frontend-api.dll");
    if (!obs || !frontend) return false;
#define LOAD(target, library, name) do { *(FARPROC *)&target = GetProcAddress(library, name); if (!target) return false; } while (0)
    LOAD(get_version, obs, "obs_get_version_string");
    if (strcmp(get_version(), "32.2.2") != 0) return false;
    LOAD(add_packet, obs, "obs_output_add_packet_callback");
    LOAD(remove_packet, obs, "obs_output_remove_packet_callback");
    LOAD(release_output, obs, "obs_output_release");
    LOAD(get_output, frontend, "obs_frontend_get_recording_output");
    LOAD(add_event, frontend, "obs_frontend_add_event_callback");
    LOAD(remove_event, frontend, "obs_frontend_remove_event_callback");
    rows = calloc(CAPACITY, sizeof(*rows));
    if (!rows) return false;
    add_event(frontend_event, NULL);
    registered = true;
    return true;
}
API void obs_module_unload(void) {
    if (registered) remove_event(frontend_event, NULL);
    /* An interrupted recording must never look complete. */
    if (output && !finished) { missing = true; finish(); }
    free(rows);
    rows = NULL;
}
