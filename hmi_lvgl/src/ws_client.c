#include "ws_client.h"
#include "lvgl.h"
#include "preview.h"
#include "ui_state.h"
#include "ws.h"
#include <stdio.h>
#include <string.h>

static ws_client_t *s_ws;
static uint32_t s_id;
static bool s_open;

static void on_text(void *ud, const char *text, size_t len)
{
    (void)ud;
    ui_state_apply(text, len);
}

static void on_bin(void *ud, const uint8_t *data, size_t len)
{
    (void)ud;
    preview_ingest(data, len);
}

static void on_open(void *ud)
{
    (void)ud;
    s_open = true;
    ui_state_set_connected(true);
}

bool link_start(const char *url)
{
    s_ws = ws_client_create(url, on_text, NULL);
    if(!s_ws) return false;
    ws_client_set_open(s_ws, on_open);
    ws_client_set_binary(s_ws, on_bin);
    return true;
}

void link_poll(void)
{
    if(!s_ws) return;
    ws_state_t st = ws_client_poll(s_ws, lv_tick_get());
    if(st != WS_OPEN && s_open) {
        s_open = false;
        ui_state_set_connected(false);
    }
}

void link_stop(void)
{
    if(s_ws) ws_client_destroy(s_ws);
    s_ws = NULL;
}

bool link_cmd(const char *name, const char *args_json)
{
    if(!s_ws || !s_open) return false;
    char buf[768];
    int n = snprintf(buf, sizeof buf, "{\"v\":1,\"type\":\"cmd\",\"id\":\"c%u\",\"name\":\"%s\",\"args\":%s}", ++s_id, name,
                     args_json ? args_json : "{}");
    if(n <= 0 || (size_t)n >= sizeof buf) return false;
    return ws_client_send(s_ws, WS_OP_TEXT, buf, (size_t)n);
}

bool link_cmd_str(const char *name, const char *key, const char *value)
{
    char esc[400];
    size_t j = 0;
    for(const char *p = value; *p && j < sizeof esc - 8; p++) {
        unsigned char c = (unsigned char)*p;
        if(c == '"' || c == '\\') { esc[j++] = '\\'; esc[j++] = (char)c; }
        else if(c < 0x20) j += (size_t)snprintf(esc + j, 8, "\\u%04x", c);
        else esc[j++] = (char)c;
    }
    esc[j] = 0;
    char args[512];
    snprintf(args, sizeof args, "{\"%s\":\"%s\"}", key, esc);
    return link_cmd(name, args);
}

bool link_cmd_bool(const char *name, const char *key, bool value)
{
    char args[96];
    snprintf(args, sizeof args, "{\"%s\":%s}", key, value ? "true" : "false");
    return link_cmd(name, args);
}

bool link_cmd_str_int(const char *name, const char *skey, const char *sval, const char *ikey, int ival)
{
    char esc[400];
    size_t j = 0;
    for(const char *p = sval; *p && j < sizeof esc - 8; p++) {
        if(*p == '"' || *p == '\\') esc[j++] = '\\';
        esc[j++] = *p;
    }
    esc[j] = 0;
    char args[512];
    snprintf(args, sizeof args, "{\"%s\":\"%s\",\"%s\":%d}", skey, esc, ikey, ival);
    return link_cmd(name, args);
}
