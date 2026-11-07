#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Link to DriveOS's /v1/ui WebSocket: polled from an LVGL timer (no threads), reconnects with backoff 0.5 -> 5 s.
   Every server message goes through ui_state_apply(); the connected flag follows the socket. */
bool link_start(const char *url);                 /* false for a bad url */
void link_poll(void);                             /* call every ~10 ms */
void link_stop(void);
bool link_cmd(const char *name, const char *args_json);   /* args_json: "{...}" or NULL for {} ; false when not connected */
bool link_cmd_str(const char *name, const char *key, const char *value);   /* {"key":"value"} with JSON escaping */
bool link_cmd_bool(const char *name, const char *key, bool value);
bool link_cmd_str_int(const char *name, const char *skey, const char *sval, const char *ikey, int ival);   /* {"skey":"sval","ikey":ival} */
bool link_cmd_str_bool(const char *name, const char *skey, const char *sval, const char *bkey, bool bval);
bool link_cmd_str3(const char *name, const char *k1, const char *v1, const char *k2, const char *v2,
                   const char *k3, const char *v3);
bool link_cmd_str3_bool(const char *name, const char *k1, const char *v1, const char *k2, const char *v2,
                        const char *k3, const char *v3, const char *bkey, bool bval);
