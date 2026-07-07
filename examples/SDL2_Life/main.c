/*
 * Conway's Game of Life for CardputerZero (320x170, SDL2).
 *
 * Ported from the Phoebe LVGL app (app_life). A toroidal cellular automaton
 * that auto-reseeds when the population stalls, goes extinct, or ages out.
 * No network or sensors. Cells are drawn as filled rects scaled to the panel.
 *
 * Keys: SPACE reseed, P pause, ESC / Q quit.
 */
#include <SDL2/SDL.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#define SCREEN_W 320
#define SCREEN_H 170

/* Grid: cells are CELL x CELL px. 80x42 grid at CELL=4 fills 320x168. */
#define CELL 4
#define GW   (SCREEN_W / CELL)   /* 80 */
#define GH   (SCREEN_H / CELL)   /* 42 */

#define STEP_MS 90               /* generation interval */

static uint8_t cur[GW * GH];
static uint8_t nxt[GW * GH];

static uint32_t rng_state;
static uint32_t xrand(void) {
    uint32_t x = rng_state;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    rng_state = x;
    return x;
}

static int gen, stall, prev_pop;

static void seed(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    rng_state ^= ((uint32_t)ts.tv_nsec * 2654435761u) | 1u;
    for (int i = 0; i < GW * GH; i++)
        cur[i] = (xrand() % 100) < 28 ? 1 : 0;
    gen = 0;
    stall = 0;
    prev_pop = -1;
}

static void step(void) {
    int pop = 0;
    for (int y = 0; y < GH; y++) {
        const int yu = (y + GH - 1) % GH;
        const int yd = (y + 1) % GH;
        for (int x = 0; x < GW; x++) {
            const int xl = (x + GW - 1) % GW;
            const int xr = (x + 1) % GW;
            const int n = cur[yu * GW + xl] + cur[yu * GW + x] + cur[yu * GW + xr]
                        + cur[y  * GW + xl]                    + cur[y  * GW + xr]
                        + cur[yd * GW + xl] + cur[yd * GW + x] + cur[yd * GW + xr];
            const uint8_t alive = cur[y * GW + x];
            const uint8_t next = (n == 3 || (alive && n == 2)) ? 1 : 0;
            nxt[y * GW + x] = next;
            pop += next;
        }
    }
    memcpy(cur, nxt, sizeof(cur));

    if (pop == prev_pop) stall++;
    else stall = 0;
    prev_pop = pop;
    if (pop == 0 || stall > 14 || ++gen > 600) seed();
}

static void draw(SDL_Renderer *ren) {
    SDL_SetRenderDrawColor(ren, 0, 0, 0, 255);   /* COLOR_BG */
    SDL_RenderClear(ren);
    SDL_SetRenderDrawColor(ren, 0x99, 0xFF, 0x00, 255); /* COLOR_ACCENT */
    for (int y = 0; y < GH; y++) {
        for (int x = 0; x < GW; x++) {
            if (!cur[y * GW + x]) continue;
            SDL_Rect r = { x * CELL, y * CELL, CELL - 1, CELL - 1 };
            SDL_RenderFillRect(ren, &r);
        }
    }
    SDL_RenderPresent(ren);
}

int main(int argc, char **argv) {
    (void)argc; (void)argv;

    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS) != 0) {
        fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
        return 1;
    }

    SDL_Window *win = SDL_CreateWindow("Life",
        SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
        SCREEN_W, SCREEN_H, SDL_WINDOW_BORDERLESS);
    if (!win) { fprintf(stderr, "SDL_CreateWindow: %s\n", SDL_GetError());
                SDL_Quit(); return 1; }

    SDL_Renderer *ren = SDL_CreateRenderer(win, -1, SDL_RENDERER_SOFTWARE);
    if (!ren) { fprintf(stderr, "SDL_CreateRenderer: %s\n", SDL_GetError());
                SDL_DestroyWindow(win); SDL_Quit(); return 1; }

    rng_state = 0x2545f491u;
    seed();

    int running = 1, paused = 0;
    Uint32 last_step = SDL_GetTicks();
    while (running) {
        Uint32 now = SDL_GetTicks();
        SDL_Event ev;
        while (SDL_PollEvent(&ev)) {
            if (ev.type == SDL_QUIT) running = 0;
            else if (ev.type == SDL_KEYDOWN) {
                SDL_Keycode k = ev.key.keysym.sym;
                if (k == SDLK_ESCAPE || k == SDLK_q) running = 0;
                else if (k == SDLK_SPACE) seed();
                else if (k == SDLK_p) paused = !paused;
            }
        }

        if (!paused && now - last_step >= STEP_MS) {
            step();
            last_step = now;
        }
        draw(ren);

        Uint32 elapsed = SDL_GetTicks() - now;
        if (elapsed < 16) SDL_Delay(16 - elapsed);
    }

    SDL_DestroyRenderer(ren);
    SDL_DestroyWindow(win);
    SDL_Quit();
    return 0;
}
