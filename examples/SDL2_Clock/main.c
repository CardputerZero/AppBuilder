/*
 * Multi-face clock for CardputerZero (320x170, SDL2).
 *
 * Ported from the Phoebe LVGL app (app_clock). Six switchable faces:
 *   analog, digital, animated (spinning arc), 7-segment, VFD dot-matrix, flip.
 * Local time only -- no network. The 7-seg / VFD glyph tables and theme colors
 * come straight from Phoebe's ui_common.h.
 *
 * Keys: SPACE / RIGHT next face, LEFT prev face, ESC / Q quit.
 */
#include <SDL2/SDL.h>
#include <SDL2/SDL_ttf.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define SCREEN_W 320
#define SCREEN_H 170
#define TICK_MS  16

#define FONT_PATH_1 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
#define FONT_PATH_2 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

/* ---- theme (from ui_common.h) ---- */
static const SDL_Color COL_FG        = { 0xE6, 0xE6, 0xE6, 255 };
static const SDL_Color COL_DIM       = { 0x9A, 0x9A, 0x9A, 255 };
static const SDL_Color COL_ACCENT    = { 0x99, 0xFF, 0x00, 255 };
static const SDL_Color COL_BAR_BG    = { 0x33, 0x33, 0x38, 255 };
static const SDL_Color SEG7_ON       = { 0xFF, 0x30, 0x30, 255 };
static const SDL_Color SEG7_OFF      = { 0x30, 0x05, 0x05, 255 };
static const SDL_Color SEG7_BG       = { 0x0A, 0x00, 0x00, 255 };
static const SDL_Color SEG7_DATE     = { 0xA0, 0x30, 0x30, 255 };
static const SDL_Color VFD_BG        = { 0x00, 0x08, 0x10, 255 };
static const SDL_Color VFD_ON        = { 0x66, 0xFF, 0xCC, 255 };
static const SDL_Color VFD_OFF       = { 0x0E, 0x18, 0x18, 255 };
static const SDL_Color VFD_DIM       = { 0x3F, 0xAA, 0x88, 255 };

/* segment order a b c d e f g */
static const uint8_t SEG7_DIGITS[10] = {
    0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x7F, 0x6F,
};
/* VFD 5x7: 7 rows of 5 bits (MSB=leftmost), idx 0..9='0'..'9', 10=':' */
static const uint8_t VFD_FONT[11][7] = {
    {0x0E,0x11,0x11,0x11,0x11,0x11,0x0E},
    {0x04,0x0C,0x04,0x04,0x04,0x04,0x0E},
    {0x0E,0x11,0x01,0x02,0x04,0x08,0x1F},
    {0x0E,0x11,0x01,0x06,0x01,0x11,0x0E},
    {0x11,0x11,0x11,0x1F,0x01,0x01,0x01},
    {0x1F,0x10,0x10,0x1E,0x01,0x11,0x0E},
    {0x0E,0x10,0x10,0x1E,0x11,0x11,0x0E},
    {0x1F,0x01,0x02,0x04,0x08,0x08,0x08},
    {0x0E,0x11,0x11,0x0E,0x11,0x11,0x0E},
    {0x0E,0x11,0x11,0x0F,0x01,0x01,0x0E},
    {0x00,0x04,0x00,0x00,0x00,0x04,0x00},
};

enum Face { F_ANALOG, F_DIGITAL, F_ANIMATED, F_SEG7, F_VFD, F_FLIP, F_COUNT };
static const char *FACE_NAME[F_COUNT] =
    { "Analog", "Digital", "Animated", "Seg7", "VFD", "Flip" };

static TTF_Font *font_sm, *font_md, *font_lg;

static void fill(SDL_Renderer *r, int x, int y, int w, int h, SDL_Color c) {
    SDL_SetRenderDrawColor(r, c.r, c.g, c.b, c.a);
    SDL_Rect rect = { x, y, w, h };
    SDL_RenderFillRect(r, &rect);
}

/* thick line via a small perpendicular fan (SDL has no native width) */
static void thick_line(SDL_Renderer *r, int x0, int y0, int x1, int y1,
                       int w, SDL_Color c) {
    SDL_SetRenderDrawColor(r, c.r, c.g, c.b, c.a);
    for (int dx = -w / 2; dx <= w / 2; dx++)
        for (int dy = -w / 2; dy <= w / 2; dy++)
            SDL_RenderDrawLine(r, x0 + dx, y0 + dy, x1 + dx, y1 + dy);
}

typedef enum { AL_L, AL_C, AL_R } Align;
static void text(SDL_Renderer *r, TTF_Font *f, const char *s,
                 int x, int y, SDL_Color c, Align a) {
    if (!f || !s || !*s) return;
    SDL_Surface *surf = TTF_RenderUTF8_Blended(f, s, c);
    if (!surf) return;
    SDL_Texture *tex = SDL_CreateTextureFromSurface(r, surf);
    int w = surf->w, h = surf->h;
    SDL_FreeSurface(surf);
    if (!tex) return;
    int px = (a == AL_C) ? x - w / 2 : (a == AL_R) ? x - w : x;
    SDL_Rect dst = { px, y, w, h };
    SDL_RenderCopy(r, tex, NULL, &dst);
    SDL_DestroyTexture(tex);
}

/* one 7-seg digit at (x,y), cell dw x dh, stroke t */
static void seg7_digit(SDL_Renderer *r, int x, int y, int d,
                       int dw, int dh, int t) {
    uint8_t m = (d >= 0 && d <= 9) ? SEG7_DIGITS[d] : 0;
    int mid = dh / 2;
    #define SEG(x0,y0,x1,y1,bit) \
        fill(r, x+(x0), y+(y0), (x1)-(x0), (y1)-(y0), (m&(1<<(bit)))?SEG7_ON:SEG7_OFF)
    SEG(t, 0, dw - t, t, 0);            /* a top */
    SEG(dw - t, t, dw, mid, 1);         /* b top-right */
    SEG(dw - t, mid, dw, dh - t, 2);    /* c bot-right */
    SEG(t, dh - t, dw - t, dh, 3);      /* d bottom */
    SEG(0, mid, t, dh - t, 4);          /* e bot-left */
    SEG(0, t, t, mid, 5);               /* f top-left */
    SEG(t, mid - t / 2, dw - t, mid + t / 2, 6); /* g middle */
    #undef SEG
}

static void seg7_colon(SDL_Renderer *r, int x, int y, int w, int h, int on) {
    int dot = 6;
    SDL_Color c = on ? SEG7_ON : SEG7_OFF;
    int cx = x + (w - dot) / 2;
    fill(r, cx, y + h / 3 - dot / 2, dot, dot, c);
    fill(r, cx, y + 2 * h / 3 - dot / 2, dot, dot, c);
}

static void vfd_glyph(SDL_Renderer *r, int x, int y, int idx, int dot, int pitch) {
    if (idx < 0 || idx > 10) return;
    for (int row = 0; row < 7; row++) {
        uint8_t bits = VFD_FONT[idx][row];
        for (int col = 0; col < 5; col++) {
            int on = bits & (1 << (4 - col));
            fill(r, x + col * pitch, y + row * pitch, dot, dot, on ? VFD_ON : VFD_OFF);
        }
    }
}

static void date_str(char *buf, size_t n, const struct tm *t) {
    snprintf(buf, n, "%04d-%02d-%02d", t->tm_year + 1900, t->tm_mon + 1, t->tm_mday);
}

/* ------------------------------ faces ------------------------------ */

static void draw_analog(SDL_Renderer *r, const struct tm *t) {
    fill(r, 0, 0, SCREEN_W, SCREEN_H, (SDL_Color){0,0,0,255});
    const int cx = SCREEN_W / 2, cy = SCREEN_H / 2 - 6;
    const int R = SCREEN_H / 2 - 14;
    /* dial ticks */
    SDL_SetRenderDrawColor(r, COL_BAR_BG.r, COL_BAR_BG.g, COL_BAR_BG.b, 255);
    for (int i = 0; i < 60; i++) {
        double a = i * 6.0 * M_PI / 180.0;
        int len = (i % 5 == 0) ? 6 : 3;
        int x0 = cx + (int)((R - len) * sin(a)), y0 = cy - (int)((R - len) * cos(a));
        int x1 = cx + (int)(R * sin(a)),         y1 = cy - (int)(R * cos(a));
        SDL_RenderDrawLine(r, x0, y0, x1, y1);
    }
    int hour = t->tm_hour % 12, mn = t->tm_min, sc = t->tm_sec;
    double ha = (hour + mn / 60.0) * 30.0 * M_PI / 180.0;
    double ma = (mn + sc / 60.0) * 6.0 * M_PI / 180.0;
    double sa = sc * 6.0 * M_PI / 180.0;
    thick_line(r, cx, cy, cx + (int)(R * 0.50 * sin(ha)), cy - (int)(R * 0.50 * cos(ha)), 5, COL_FG);
    thick_line(r, cx, cy, cx + (int)(R * 0.74 * sin(ma)), cy - (int)(R * 0.74 * cos(ma)), 3, COL_FG);
    thick_line(r, cx, cy, cx + (int)(R * 0.90 * sin(sa)), cy - (int)(R * 0.90 * cos(sa)), 1, COL_ACCENT);
    fill(r, cx - 4, cy - 4, 8, 8, COL_ACCENT);
    char buf[40]; date_str(buf, sizeof(buf), t);
    text(r, font_sm, buf, SCREEN_W / 2, SCREEN_H - 20, COL_DIM, AL_C);
}

static void draw_digital(SDL_Renderer *r, const struct tm *t) {
    fill(r, 0, 0, SCREEN_W, SCREEN_H, (SDL_Color){0,0,0,255});
    char hm[8], sec[8], d[40];
    snprintf(hm, sizeof(hm), "%02d:%02d", t->tm_hour, t->tm_min);
    snprintf(sec, sizeof(sec), ":%02d", t->tm_sec);
    date_str(d, sizeof(d), t);
    text(r, font_lg, hm, SCREEN_W / 2, 34, COL_FG, AL_C);
    text(r, font_md, sec, SCREEN_W / 2, 96, COL_ACCENT, AL_C);
    text(r, font_sm, d, SCREEN_W / 2, SCREEN_H - 22, COL_DIM, AL_C);
}

static void draw_animated(SDL_Renderer *r, const struct tm *t, Uint32 now) {
    fill(r, 0, 0, SCREEN_W, SCREEN_H, (SDL_Color){0,0,0,255});
    const int cx = SCREEN_W / 2, cy = SCREEN_H / 2 - 6, R = 74;
    /* background ring */
    SDL_SetRenderDrawColor(r, COL_BAR_BG.r, COL_BAR_BG.g, COL_BAR_BG.b, 255);
    for (double a = 0; a < 2 * M_PI; a += 0.03)
        for (int w = 0; w < 8; w++)
            SDL_RenderDrawPoint(r, cx + (int)((R - w) * cos(a)), cy + (int)((R - w) * sin(a)));
    /* spinning 60-degree accent segment, ~2.4s per revolution */
    double base = (now % 2400) / 2400.0 * 2 * M_PI;
    SDL_SetRenderDrawColor(r, COL_ACCENT.r, COL_ACCENT.g, COL_ACCENT.b, 255);
    for (double a = base; a < base + M_PI / 3; a += 0.02)
        for (int w = 0; w < 8; w++)
            SDL_RenderDrawPoint(r, cx + (int)((R - w) * cos(a)), cy + (int)((R - w) * sin(a)));
    char hm[8], d[40];
    snprintf(hm, sizeof(hm), "%02d:%02d", t->tm_hour, t->tm_min);
    date_str(d, sizeof(d), t);
    text(r, font_md, hm, cx, cy - 18, COL_FG, AL_C);
    text(r, font_sm, d, SCREEN_W / 2, SCREEN_H - 22, COL_DIM, AL_C);
}

static void draw_seg7(SDL_Renderer *r, const struct tm *t) {
    fill(r, 0, 0, SCREEN_W, SCREEN_H, SEG7_BG);
    const int DW = 42, DH = 82, T = 8, GAP = 8, COLON_W = 18;
    int total = 4 * DW + COLON_W + 4 * GAP;
    int x = (SCREEN_W - total) / 2, y = (SCREEN_H - DH) / 2 - 8;
    seg7_digit(r, x, y, t->tm_hour / 10, DW, DH, T); x += DW + GAP;
    seg7_digit(r, x, y, t->tm_hour % 10, DW, DH, T); x += DW + GAP;
    seg7_colon(r, x, y, COLON_W, DH, t->tm_sec % 2 == 0); x += COLON_W + GAP;
    seg7_digit(r, x, y, t->tm_min / 10, DW, DH, T); x += DW + GAP;
    seg7_digit(r, x, y, t->tm_min % 10, DW, DH, T);
    char d[40]; date_str(d, sizeof(d), t);
    text(r, font_sm, d, SCREEN_W / 2, SCREEN_H - 20, SEG7_DATE, AL_C);
}

static void draw_vfd(SDL_Renderer *r, const struct tm *t) {
    fill(r, 0, 0, SCREEN_W, SCREEN_H, VFD_BG);
    const int DOT = 7, PITCH = 9;
    const int GLYPH_W = 5 * PITCH, GAP = 9;
    int total = 5 * GLYPH_W + 4 * GAP, gh = 7 * PITCH;
    int x = (SCREEN_W - total) / 2, y = (SCREEN_H - gh) / 2 - 8;
    int idx[5] = { t->tm_hour / 10, t->tm_hour % 10, 10, t->tm_min / 10, t->tm_min % 10 };
    for (int i = 0; i < 5; i++) { vfd_glyph(r, x, y, idx[i], DOT, PITCH); x += GLYPH_W + GAP; }
    char d[40]; date_str(d, sizeof(d), t);
    text(r, font_sm, d, SCREEN_W / 2, SCREEN_H - 20, VFD_DIM, AL_C);
}

static void draw_flip(SDL_Renderer *r, const struct tm *t) {
    fill(r, 0, 0, SCREEN_W, SCREEN_H, (SDL_Color){0,0,0,255});
    const int cw = 108, ch = 108, gap = 16;
    int y = (SCREEN_H - ch) / 2 - 6;
    int x0 = SCREEN_W / 2 - cw - gap / 2;
    int x1 = SCREEN_W / 2 + gap / 2;
    SDL_Color card = { 0x1d, 0x1d, 0x2b, 255 };
    char hh[4], mm[4];
    snprintf(hh, sizeof(hh), "%02d", t->tm_hour);
    snprintf(mm, sizeof(mm), "%02d", t->tm_min);
    fill(r, x0, y, cw, ch, card);
    fill(r, x1, y, cw, ch, card);
    fill(r, x0, y + ch / 2 - 1, cw, 2, (SDL_Color){0,0,0,255}); /* seam */
    fill(r, x1, y + ch / 2 - 1, cw, 2, (SDL_Color){0,0,0,255});
    text(r, font_lg, hh, x0 + cw / 2, y + ch / 2 - 34, COL_FG, AL_C);
    text(r, font_lg, mm, x1 + cw / 2, y + ch / 2 - 34, COL_FG, AL_C);
    char d[40]; date_str(d, sizeof(d), t);
    text(r, font_sm, d, SCREEN_W / 2, SCREEN_H - 22, COL_DIM, AL_C);
}

int main(int argc, char **argv) {
    (void)argc; (void)argv;
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS) != 0) {
        fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    if (TTF_Init() != 0) {
        fprintf(stderr, "TTF_Init: %s\n", TTF_GetError());
        SDL_Quit(); return 1;
    }
    SDL_Window *win = SDL_CreateWindow("Clock",
        SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
        SCREEN_W, SCREEN_H, SDL_WINDOW_BORDERLESS);
    if (!win) { fprintf(stderr, "SDL_CreateWindow: %s\n", SDL_GetError());
                TTF_Quit(); SDL_Quit(); return 1; }
    SDL_Renderer *ren = SDL_CreateRenderer(win, -1, SDL_RENDERER_SOFTWARE);
    if (!ren) { fprintf(stderr, "SDL_CreateRenderer: %s\n", SDL_GetError());
                SDL_DestroyWindow(win); TTF_Quit(); SDL_Quit(); return 1; }

    const char *fp = FONT_PATH_1;
    font_sm = TTF_OpenFont(fp, 14);
    font_md = TTF_OpenFont(fp, 30);
    font_lg = TTF_OpenFont(FONT_PATH_2, 56);
    if (!font_lg) font_lg = TTF_OpenFont(fp, 56);
    if (!font_sm) fprintf(stderr, "TTF_OpenFont: %s\n", TTF_GetError());

    enum Face face = F_ANALOG;
    /* Optional starting face: CLOCK_FACE=analog|digital|animated|seg7|vfd|flip */
    const char *ef = getenv("CLOCK_FACE");
    if (ef) {
        for (int i = 0; i < F_COUNT; i++) {
            if (SDL_strcasecmp(ef, FACE_NAME[i]) == 0) { face = (enum Face)i; break; }
        }
    }
    Uint32 label_until = 0;
    int running = 1;
    while (running) {
        Uint32 now = SDL_GetTicks();
        SDL_Event ev;
        while (SDL_PollEvent(&ev)) {
            if (ev.type == SDL_QUIT) running = 0;
            else if (ev.type == SDL_KEYDOWN) {
                SDL_Keycode k = ev.key.keysym.sym;
                if (k == SDLK_ESCAPE || k == SDLK_q) running = 0;
                else if (k == SDLK_SPACE || k == SDLK_RIGHT) {
                    face = (face + 1) % F_COUNT; label_until = now + 1200;
                } else if (k == SDLK_LEFT) {
                    face = (face + F_COUNT - 1) % F_COUNT; label_until = now + 1200;
                }
            }
        }

        time_t tt = time(NULL);
        struct tm *t = localtime(&tt);
        if (t) {
            switch (face) {
                case F_ANALOG:   draw_analog(ren, t); break;
                case F_DIGITAL:  draw_digital(ren, t); break;
                case F_ANIMATED: draw_animated(ren, t, now); break;
                case F_SEG7:     draw_seg7(ren, t); break;
                case F_VFD:      draw_vfd(ren, t); break;
                case F_FLIP:     draw_flip(ren, t); break;
                default: break;
            }
            if (now < label_until)
                text(ren, font_sm, FACE_NAME[face], 6, 4, COL_ACCENT, AL_L);
        }
        SDL_RenderPresent(ren);

        Uint32 el = SDL_GetTicks() - now;
        if (el < TICK_MS) SDL_Delay(TICK_MS - el);
    }

    if (font_sm) TTF_CloseFont(font_sm);
    if (font_md) TTF_CloseFont(font_md);
    if (font_lg) TTF_CloseFont(font_lg);
    SDL_DestroyRenderer(ren);
    SDL_DestroyWindow(win);
    TTF_Quit();
    SDL_Quit();
    return 0;
}
