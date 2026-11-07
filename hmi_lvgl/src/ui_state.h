#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* The UI-side mirror of DriveOS's /v1/ui contract v1: the server pushes full "state" snapshots, the client only renders
   them and sends "cmd"s back. Strings are copied into fixed buffers (truncated), so the struct is safe to hold. */
#define UI_PLACES_MAX 12
#define UI_HIGHLIGHTS_MAX 6
#define UI_SECTIONS_MAX 10
#define UI_ITEMS_MAX 80
#define UI_GROUPS_MAX 4
#define UI_OPTIONS_MAX 8
#define UI_LINES_MAX 12
#define UI_STEPS_MAX 8
#define UI_ROUTE_MAX 4096
#define UI_CONGESTION_MAX 1024
#define UI_LANES_MAX 8

typedef struct {
    char id[64], name[64], category[24], meta[64], address[96], distance_text[16];
    char img[48], img_lg[48];   /* thumbnail paths relative to hello.image_root; empty = none */
    int eta_min;
    bool orderable, sample;     /* sample: its menu is a labelled demo menu, not the business's own */
} ui_place_t;
typedef struct {
    char id[64], name[64], category[24], distance_text[16], img[48], img_lg[48];
    bool orderable, sample;
} ui_home_place_t;

typedef struct { char id[96], name[96], price_text[16], desc[160], img[48], img_lg[48]; int section; bool addable; } ui_menu_item_t;
typedef struct { char id[48], name[48], delta[16], img[48]; bool on; } ui_option_t;
typedef struct { char id[32], name[48]; ui_option_t options[UI_OPTIONS_MAX]; int n; } ui_group_t;
typedef struct { char id[48], name[96], price_text[16], img[48], opts[96]; int qty; } ui_line_t;
typedef struct { char state[16], label[48], phase[8], at[24]; } ui_step_t;   /* phase: done | now | next */

typedef enum {
    UI_AUTH_NONE, UI_AUTH_CHALLENGE, UI_AUTH_FACE, UI_AUTH_OTP, UI_AUTH_VERIFYING, UI_AUTH_ACCEPTED, UI_AUTH_REJECTED
} ui_auth_stage_t;

typedef struct {
    bool connected;           /* WebSocket open (or a fixture is loaded) */
    bool fixture;             /* loaded from --fixture, no WebSocket */
    uint32_t rev;             /* bumps on every change the UI should redraw */
    uint32_t seq;

    struct { char app[24], version[24], build[24], image_root[192]; bool have; } hello;
    char tab[12];             /* home | agent | shop | nav */

    struct { char state[16]; float level; char partial[256], heard[256], reply[512]; bool agent_offline; } voice;   /* idle|listening|thinking|speaking */
    struct { float speed_mps; bool parked; double lat, lon; float heading; double t_ms; char label[96]; char source[8], source_text[48]; } vehicle;   /* source: gps | sim */
    /* Real places near the car for the Home strip (state.home.places). */
    struct { ui_home_place_t places[8]; int n; } home;

    /* Everything under shop is zeroed and re-parsed on each snapshot, so its bytes can be hashed to detect changes. */
    struct {
        char view[16];        /* idle | places | menu | item | cart | checkout | order */
        char status[96];
        ui_place_t places[UI_PLACES_MAX]; int n_places;
        char place_id[64], place_name[64];
        bool sample;          /* the open place shows a sample (demo) menu */
        char sections[UI_SECTIONS_MAX][48]; int n_sections;
        ui_menu_item_t items[UI_ITEMS_MAX]; int n_items;
        struct {
            bool open; char id[96], name[96], desc[256], price_text[16], total_text[16], img_lg[48];
            int qty; ui_group_t groups[UI_GROUPS_MAX]; int n_groups;
        } item;
        ui_line_t lines[UI_LINES_MAX]; int cart_lines;   /* cart_lines = number of line entries */
        int cart_count;                                   /* sum of quantities */
        char cart_place[64]; double subtotal, tax, total; char cart_total_text[24];
        bool checkout_pending; char checkout_amount_text[24], checkout_merchant[64];
        bool method_voice, method_face, method_code;
        bool info_present, face_available; bool voice_consent; char voice_sub[48], face_sub[48], code_sub[48];   /* method_info */
        ui_auth_stage_t auth_stage; char auth_challenge[96], auth_detail[128];
        bool auth_preview, voice_entry; char face_state[12], face_name[48];   /* checkout.auth: camera running; scanning|verified|failed|none */
        struct {
            bool present, terminal; char id[48], status[24], status_text[48], pickup_code[24], merchant[64];
            double eta_min;       /* -1 when unknown */
            ui_step_t steps[UI_STEPS_MAX]; int n_steps;
        } order;
    } shop;
    struct { bool valid; float x, y, w, h; } face_rect;   /* checkout.auth.face_rect, normalised 0..1; not part of the shop hash */
    /* eta_min -1 unknown; mode idle|overview|navigating|arrived. route_ll/route_n: the decoded optional
       nav.route.polyline6 as (lon, lat), replaced only when the encoded text changes (route_ver bumps then);
       congestion: Directions' one letter per segment (u l m h s), from the optional nav.congestion string. */
    struct {
        bool active; char dest[64]; char maneuver[96]; double eta_min; char mode[16];
        double (*route_ll)[2]; int route_n; uint32_t route_ver, route_hash, route_id; char route_reason[12]; char congestion[UI_CONGESTION_MAX];
        /* optional nav.step {icon,text,dist_m}: the upcoming maneuver; step_icon empty when absent */
        char step_icon[20], step_text[96]; double step_dist_m;
        /* optional nav.step.lanes: ind = bit per dos_lane_dir_t, active = the arrow the route takes (-1 none) */
        struct { uint16_t ind; int8_t active; bool valid; } lanes[UI_LANES_MAX]; int n_lanes;
    } nav;
    struct { bool active; char text[128]; char kind[12]; } toast;
    /* state.phone: the demo phone notification carrying the one-time code (null after 5 min) */
    struct { bool active; char title[32], text[160], code[16]; } phone;
    /* Voice and face enrollment state, zeroed and re-parsed per snapshot. */
    struct {
        bool active; char stage[12];   /* ready | recording | analyzing | done | failed */
        int take, total, kept; char prompt[160], error[128];
        struct { float duration_s, snr_db, clipping_pct; bool outlier, ok; } q[6]; int n_q;
    } enroll;
    struct { bool enrolled, linked; char name[48]; } face_id;
    struct {
        int n, max; char selected_id[48];
        struct { char id[48], name[48], role[12]; bool linked; } items[5];
    } face_profiles;
    struct {
        bool active; char operation[12], stage[16], name[48], error[128];
    } enroll_face;
    struct { bool available; char version[24]; char highlights[UI_HIGHLIGHTS_MAX][96]; int n; } update;
} ui_state_t;

const ui_state_t *ui_state(void);
/* Parse one server message (hello | state | event | ack); true when the UI must refresh. */
bool ui_state_apply(const char *text, size_t len);
/* Snapshots applied since the last call, their mean and worst parse time (DOS_MAP_STATS). */
void ui_state_take_parse_stats(unsigned *n, double *avg_ms, double *max_ms);
/* Load a fixture: a full {"type":"state",...} message, or a bare state object; an optional top-level "hello" object. */
bool ui_state_load_fixture(const char *path);
void ui_state_set_connected(bool on);
/* Local, optimistic change (a tab press before the server echoes it). */
void ui_state_set_tab(const char *tab);
const char *ui_state_last_error(void);
uint32_t ui_shop_hash(void);   /* FNV-1a over the shop block: changes only when something the shop screens show changed */
uint32_t ui_enroll_hash(void);
