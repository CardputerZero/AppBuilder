/*
 * Matrix digital rain for CardputerZero (320x170, SDL2).
 *
 * Ported from the Phoebe LVGL app (app_matrix). Falling columns of green
 * glyphs on a dark base -- pure eye-candy, no network or sensors. Renders in
 * the desktop emulator window and on-device via the KMSDRM/Wayland backend.
 *
 * Keys: ESC / Q quit.
 */
#include <SDL2/SDL.h>
#include <SDL2/SDL_ttf.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define SCREEN_W 320
#define SCREEN_H 170
#define TICK_MS  16

#define FONT_PATH_1 "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
#define FONT_PATH_2 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

#define GLYPH_W  10   /* column pitch in px */
#define GLYPH_H  14   /* row pitch in px    */
#define COLS     (SCREEN_W / GLYPH_W)
#define ROWS     (SCREEN_H / GLYPH_H + 2)

static const char CHARSET[] =
    "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ#$%&*+=<>?";
static const int CHARSET_N = (int)sizeof(CHARSET) - 1;

/* xorshift32 -- same generator the original app used. */
static uint32_t rng_state = 0x1234abcdu;
static uint32_t xrand(void) {
    uint32_t x = rng_state;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    rng_state = x;
    return x;
}

typedef struct {
    float head;     /* y of the leading glyph, in rows (may be negative) */
    float speed;    /* rows per tick */
    int   len;      /* trail length in rows */
    char  glyph[ROWS + 4];
} Column;

static Column cols[COLS];

static void column_reset(Column *c, int seed_above) {
    c->head  = seed_above ? -(float)(xrand() % (ROWS * 2)) : 0.0f;
    c->speed = 0.15f + (xrand() % 100) / 300.0f;   /* 0.15 .. ~0.48 rows/tick */
    c->len   = 5 + (int)(xrand() % (ROWS - 4));
    for (int i = 0; i < ROWS + 4; i++)
        c->glyph[i] = CHARSET[xrand() % CHARSET_N];
}

static void draw_glyph(SDL_Renderer *ren, TTF_Font *font, char ch,
                       int px, int py, SDL_Color c) {
    if (!font) return;
    char s[2] = { ch, 0 };
    SDL_Surface *surf = TTF_RenderUTF8_Blended(font, s, c);
    if (!surf) return;
    SDL_Texture *tex = SDL_CreateTextureFromSurface(ren, surf);
    SDL_Rect dst = { px, py, surf->w, surf->h };
    SDL_FreeSurface(surf);
    if (!tex) return;
    SDL_RenderCopy(ren, tex, NULL, &dst);
    SDL_DestroyTexture(tex);
}

int main(int argc, char **argv) {
    (void)argc; (void)argv;

    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS) != 0) {
        fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    if (TTF_Init() != 0) {
        fprintf(stderr, "TTF_Init: %s\n", TTF_GetError());
        SDL_Quit();
        return 1;
    }

    SDL_Window *win = SDL_CreateWindow("Matrix",
        SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
        SCREEN_W, SCREEN_H, SDL_WINDOW_BORDERLESS);
    if (!win) { fprintf(stderr, "SDL_CreateWindow: %s\n", SDL_GetError());
                TTF_Quit(); SDL_Quit(); return 1; }

    SDL_Renderer *ren = SDL_CreateRenderer(win, -1, SDL_RENDERER_SOFTWARE);
    if (!ren) { fprintf(stderr, "SDL_CreateRenderer: %s\n", SDL_GetError());
                SDL_DestroyWindow(win); TTF_Quit(); SDL_Quit(); return 1; }

    TTF_Font *font = TTF_OpenFont(FONT_PATH_1, 12);
    if (!font) font = TTF_OpenFont(FONT_PATH_2, 12);
    if (!font) fprintf(stderr, "TTF_OpenFont: %s\n", TTF_GetError());

    for (int i = 0; i < COLS; i++) column_reset(&cols[i], 1);

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

        /* advance + draw */
        SDL_SetRenderDrawColor(ren, 0x00, 0x10, 0x05, 255); /* dark green base */
        SDL_RenderClear(ren);

        for (int i = 0; i < COLS; i++) {
            Column *c = &cols[i];
            c->head += c->speed;
            /* occasionally mutate a glyph so columns shimmer */
            if ((xrand() & 0x1f) == 0)
                c->glyph[xrand() % (ROWS + 4)] = CHARSET[xrand() % CHARSET_N];

            int px = i * GLYPH_W + 1;
            int head_row = (int)c->head;
            for (int t = 0; t < c->len; t++) {
                int row = head_row - t;
                if (row < 0 || row >= ROWS) continue;
                SDL_Color col;
                if (t == 0) {
                    col = (SDL_Color){ 0xCC, 0xFF, 0xCC, 255 }; /* bright head */
                } else {
                    int fade = 255 - (t * 220 / c->len);
                    if (fade < 30) fade = 30;
                    col = (SDL_Color){ 0x22, (Uint8)(0x88 + fade / 4),
                                       0x44, (Uint8)fade };
                }
                char ch = c->glyph[(row + i) % (ROWS + 4)];
                draw_glyph(ren, font, ch, px, row * GLYPH_H, col);
            }
            if (head_row - c->len > ROWS) column_reset(c, 0);
        }

        SDL_RenderPresent(ren);

        Uint32 elapsed = SDL_GetTicks() - now;
        if (elapsed < TICK_MS) SDL_Delay(TICK_MS - elapsed);
    }

    if (font) TTF_CloseFont(font);
    SDL_DestroyRenderer(ren);
    SDL_DestroyWindow(win);
    TTF_Quit();
    SDL_Quit();
    return 0;
}
