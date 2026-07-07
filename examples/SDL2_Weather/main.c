/*
 * Weather for CardputerZero (320x170, SDL2).
 *
 * Ported from the Phoebe LVGL app (app_weather). Current conditions from the
 * keyless Open-Meteo API, fetched on a background thread via libcurl, with a
 * procedurally drawn + animated icon (breathing sun / drifting cloud / falling
 * rain) chosen by WMO weather code. The WMO code->category/text tables come
 * from Phoebe's ui_common.h.
 *
 * Config via env (all optional):
 *   WEATHER_LAT, WEATHER_LON   coordinates   (default Colombo 6.93, 79.86)
 *   WEATHER_CITY               display name  (default "Colombo")
 *
 * Keys: ESC / Q quit.
 */
#include <SDL2/SDL.h>
#include <SDL2/SDL_ttf.h>
#include <curl/curl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define SCREEN_W 320
#define SCREEN_H 170
#define TICK_MS  16
#define REFRESH_MS 600000   /* refetch every 10 min */

#define FONT_PATH_1 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
#define FONT_PATH_2 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

static const SDL_Color COL_FG     = { 0xE6, 0xE6, 0xE6, 255 };
static const SDL_Color COL_DIM    = { 0x9A, 0x9A, 0x9A, 255 };
static const SDL_Color COL_ACCENT = { 0x99, 0xFF, 0x00, 255 };
static const SDL_Color COL_SUN    = { 0xFF, 0xD2, 0x40, 255 };
static const SDL_Color COL_CLOUD  = { 0xCA, 0xD2, 0xDE, 255 };
static const SDL_Color COL_RAIN   = { 0x55, 0xAA, 0xFF, 255 };

typedef enum { WX_CLEAR, WX_CLOUD, WX_RAIN } WxCat;

static WxCat wx_category(int code) {
    if (code <= 1) return WX_CLEAR;
    if (code == 2 || code == 3 || code == 45 || code == 48) return WX_CLOUD;
    return WX_RAIN;
}
static const char *wx_text(int code) {
    switch (code) {
        case 0:  return "Clear";
        case 1:  return "Mainly clear";
        case 2:  return "Partly cloudy";
        case 3:  return "Overcast";
        case 45: case 48: return "Fog";
        case 51: case 53: case 55: return "Drizzle";
        case 61: case 63: case 65: return "Rain";
        case 66: case 67: return "Freezing rain";
        case 71: case 73: case 75: case 77: return "Snow";
        case 80: case 81: case 82: return "Showers";
        case 85: case 86: return "Snow showers";
        case 95: case 96: case 99: return "Thunderstorm";
        default: return "--";
    }
}

/* ---- shared state between fetch thread and render loop ---- */
typedef struct {
    SDL_mutex *lock;
    int   ok;
    int   code;
    float temp_c, humidity, wind_kmh;
    char  err[64];
} WxState;
static WxState g;

static double CFG_LAT = 6.9271, CFG_LON = 79.8612;
static char   CFG_CITY[48] = "Colombo";

/* ---- libcurl response buffer ---- */
typedef struct { char *p; size_t n; } Buf;
static size_t on_data(void *ptr, size_t sz, size_t nm, void *ud) {
    Buf *b = (Buf *)ud;
    size_t add = sz * nm;
    char *np = realloc(b->p, b->n + add + 1);
    if (!np) return 0;
    b->p = np;
    memcpy(b->p + b->n, ptr, add);
    b->n += add;
    b->p[b->n] = 0;
    return add;
}

/* Pull a numeric field like "temperature_2m":21.4 out of the flat JSON.
 * Open-Meteo repeats each key inside "current_units" (as a string, e.g.
 * "temperature_2m":"C") before the real numeric value inside "current", so
 * scan only from the "current": object onward -- no JSON library required. */
static int json_num(const char *json, const char *key, float *out) {
    const char *cur = strstr(json, "\"current\":{");
    if (cur) cur += strlen("\"current\":{");
    else cur = json;
    char pat[64];
    snprintf(pat, sizeof(pat), "\"%s\":", key);
    const char *at = strstr(cur, pat);
    if (!at) return 0;
    at += strlen(pat);
    if (*at == '"') return 0;   /* a units string, not a number */
    *out = (float)atof(at);
    return 1;
}

static void fetch_once(void) {
    char url[256];
    snprintf(url, sizeof(url),
        "https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
        "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
        CFG_LAT, CFG_LON);

    CURL *c = curl_easy_init();
    if (!c) return;
    Buf b = { NULL, 0 };
    curl_easy_setopt(c, CURLOPT_URL, url);
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, on_data);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, &b);
    curl_easy_setopt(c, CURLOPT_TIMEOUT, 15L);
    curl_easy_setopt(c, CURLOPT_USERAGENT, "cpzero-weather/0.1");
    CURLcode rc = curl_easy_perform(c);
    long http = 0;
    curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &http);
    curl_easy_cleanup(c);

    SDL_LockMutex(g.lock);
    if (rc != CURLE_OK || http != 200 || !b.p) {
        g.ok = 0;
        snprintf(g.err, sizeof(g.err), "net err %d/%ld", (int)rc, http);
    } else {
        float t, h, code, w;
        int have = json_num(b.p, "temperature_2m", &t)
                 & json_num(b.p, "weather_code", &code)
                 & json_num(b.p, "relative_humidity_2m", &h)
                 & json_num(b.p, "wind_speed_10m", &w);
        if (have) {
            g.temp_c = t; g.humidity = h; g.wind_kmh = w; g.code = (int)code;
            g.ok = 1; g.err[0] = 0;
        } else {
            g.ok = 0;
            snprintf(g.err, sizeof(g.err), "parse err");
        }
    }
    SDL_UnlockMutex(g.lock);
    free(b.p);
}

static volatile int g_running = 1;
static int fetch_thread(void *arg) {
    (void)arg;
    Uint32 last = 0;
    while (g_running) {
        Uint32 now = SDL_GetTicks();
        if (last == 0 || now - last >= REFRESH_MS) {
            fetch_once();
            last = SDL_GetTicks();
        }
        SDL_Delay(200);
    }
    return 0;
}

/* ------------------------------ drawing ------------------------------ */
static void fill_circle(SDL_Renderer *r, int cx, int cy, int rad, SDL_Color c) {
    SDL_SetRenderDrawColor(r, c.r, c.g, c.b, c.a);
    for (int dy = -rad; dy <= rad; dy++) {
        int dx = (int)sqrt((double)rad * rad - (double)dy * dy);
        SDL_RenderDrawLine(r, cx - dx, cy + dy, cx + dx, cy + dy);
    }
}
static void fill_rrect(SDL_Renderer *r, int x, int y, int w, int h, int rad, SDL_Color c) {
    SDL_SetRenderDrawColor(r, c.r, c.g, c.b, c.a);
    SDL_Rect mid = { x, y + rad, w, h - 2 * rad };
    SDL_RenderFillRect(r, &mid);
    SDL_Rect top = { x + rad, y, w - 2 * rad, rad };
    SDL_RenderFillRect(r, &top);
    SDL_Rect bot = { x + rad, y + h - rad, w - 2 * rad, rad };
    SDL_RenderFillRect(r, &bot);
    fill_circle(r, x + rad, y + rad, rad, c);
    fill_circle(r, x + w - rad, y + rad, rad, c);
    fill_circle(r, x + rad, y + h - rad, rad, c);
    fill_circle(r, x + w - rad, y + h - rad, rad, c);
}

typedef enum { AL_L, AL_C } Align;
static void text(SDL_Renderer *r, TTF_Font *f, const char *s,
                 int x, int y, SDL_Color c, Align a) {
    if (!f || !s || !*s) return;
    SDL_Surface *surf = TTF_RenderUTF8_Blended(f, s, c);
    if (!surf) return;
    SDL_Texture *tex = SDL_CreateTextureFromSurface(r, surf);
    int w = surf->w, h = surf->h;
    SDL_FreeSurface(surf);
    if (!tex) return;
    int px = (a == AL_C) ? x - w / 2 : x;
    SDL_Rect dst = { px, y, w, h };
    SDL_RenderCopy(r, tex, NULL, &dst);
    SDL_DestroyTexture(tex);
}

static void draw_icon(SDL_Renderer *r, WxCat cat, Uint32 now) {
    const int icx = SCREEN_W / 2, icy = 50;
    if (cat == WX_CLEAR) {
        /* breathing sun: radius oscillates 23..28 over ~2.2s */
        double ph = (now % 2200) / 2200.0 * 2 * M_PI;
        int rad = 25 + (int)(2.5 * sin(ph));
        fill_circle(r, icx, icy, rad, COL_SUN);
    } else {
        /* cloud body + two puffs */
        fill_rrect(r, icx - 39, icy - 4, 78, 26, 13, COL_CLOUD);
        fill_circle(r, icx - 16, icy - 6, 14, COL_CLOUD);
        fill_circle(r, icx + 14, icy - 10, 17, COL_CLOUD);
        if (cat == WX_RAIN) {
            for (int i = 0; i < 3; i++) {
                int base = icy + 30;
                int off = (int)((now / 12 + i * 90) % 44);  /* fall + wrap */
                int dy = off < 22 ? off : 0;
                int dx = (i - 1) * 18;
                SDL_SetRenderDrawColor(r, COL_RAIN.r, COL_RAIN.g, COL_RAIN.b, 255);
                SDL_Rect drop = { icx + dx - 2, base + dy, 4, 12 };
                SDL_RenderFillRect(r, &drop);
            }
        }
    }
}

int main(int argc, char **argv) {
    (void)argc; (void)argv;

    const char *e;
    if ((e = getenv("WEATHER_LAT"))) CFG_LAT = atof(e);
    if ((e = getenv("WEATHER_LON"))) CFG_LON = atof(e);
    if ((e = getenv("WEATHER_CITY"))) { strncpy(CFG_CITY, e, sizeof(CFG_CITY) - 1); }

    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS) != 0) {
        fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    if (TTF_Init() != 0) {
        fprintf(stderr, "TTF_Init: %s\n", TTF_GetError());
        SDL_Quit(); return 1;
    }
    curl_global_init(CURL_GLOBAL_DEFAULT);

    SDL_Window *win = SDL_CreateWindow("Weather",
        SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
        SCREEN_W, SCREEN_H, SDL_WINDOW_BORDERLESS);
    if (!win) { fprintf(stderr, "SDL_CreateWindow: %s\n", SDL_GetError());
                TTF_Quit(); SDL_Quit(); return 1; }
    SDL_Renderer *ren = SDL_CreateRenderer(win, -1, SDL_RENDERER_SOFTWARE);
    if (!ren) { fprintf(stderr, "SDL_CreateRenderer: %s\n", SDL_GetError());
                SDL_DestroyWindow(win); TTF_Quit(); SDL_Quit(); return 1; }

    TTF_Font *font_sm = TTF_OpenFont(FONT_PATH_1, 14);
    TTF_Font *font_lg = TTF_OpenFont(FONT_PATH_2, 48);
    if (!font_lg) font_lg = TTF_OpenFont(FONT_PATH_1, 48);

    g.lock = SDL_CreateMutex();
    g.ok = 0;
    snprintf(g.err, sizeof(g.err), "fetching...");
    SDL_Thread *th = SDL_CreateThread(fetch_thread, "fetch", NULL);

    int running = 1;
    while (running) {
        Uint32 now = SDL_GetTicks();
        SDL_Event ev;
        while (SDL_PollEvent(&ev)) {
            if (ev.type == SDL_QUIT) running = 0;
            else if (ev.type == SDL_KEYDOWN) {
                SDL_Keycode k = ev.key.keysym.sym;
                if (k == SDLK_ESCAPE || k == SDLK_q) running = 0;
            }
        }

        WxState s;
        SDL_LockMutex(g.lock);
        s = g;
        SDL_UnlockMutex(g.lock);

        SDL_SetRenderDrawColor(ren, 10, 12, 20, 255);
        SDL_RenderClear(ren);

        text(ren, font_sm, CFG_CITY, SCREEN_W / 2, 6, COL_ACCENT, AL_C);

        WxCat cat = s.ok ? wx_category(s.code) : WX_CLOUD;
        draw_icon(ren, cat, now);

        char buf[48];
        if (s.ok) {
            snprintf(buf, sizeof(buf), "%d\xC2\xB0""C", (int)(s.temp_c + 0.5f));
            text(ren, font_lg, buf, SCREEN_W / 2, 82, COL_FG, AL_C);
            text(ren, font_sm, wx_text(s.code), SCREEN_W / 2, 138, COL_ACCENT, AL_C);
            snprintf(buf, sizeof(buf), "%d%%   %d km/h",
                     (int)(s.humidity + 0.5f), (int)(s.wind_kmh + 0.5f));
            text(ren, font_sm, buf, SCREEN_W / 2, SCREEN_H - 18, COL_DIM, AL_C);
        } else {
            text(ren, font_lg, "--", SCREEN_W / 2, 82, COL_FG, AL_C);
            text(ren, font_sm, s.err[0] ? s.err : "fetching...",
                 SCREEN_W / 2, 138, COL_DIM, AL_C);
        }

        SDL_RenderPresent(ren);
        Uint32 el = SDL_GetTicks() - now;
        if (el < TICK_MS) SDL_Delay(TICK_MS - el);
    }

    g_running = 0;
    SDL_WaitThread(th, NULL);
    SDL_DestroyMutex(g.lock);
    curl_global_cleanup();
    if (font_sm) TTF_CloseFont(font_sm);
    if (font_lg) TTF_CloseFont(font_lg);
    SDL_DestroyRenderer(ren);
    SDL_DestroyWindow(win);
    TTF_Quit();
    SDL_Quit();
    return 0;
}
