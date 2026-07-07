/*
 * nano-style text editor for CardputerZero (320x170, SDL2).
 *
 * A real, working editor: a line-array text buffer, vertical + horizontal
 * scrolling, a text cursor, insert / delete / newline, selection with
 * cut / copy / paste through the SDL clipboard, and file open / save.
 * Modelled on GNU nano's Ctrl-key command set and status bar.
 *
 * Usage:  editor [file]      (a new unnamed buffer if no file given)
 *
 * Keys (Ctrl = the device Ctrl key):
 *   arrows / PgUp / PgDn   move cursor / scroll
 *   Home / End             start / end of line   (also ^A / ^E)
 *   Shift + move           extend selection
 *   ^S  save               ^O  save-as (prompts for name)
 *   ^K  cut line/selection ^C  copy   ^U / ^V  paste
 *   ^G  select-all toggle  Backspace / Delete
 *   ^Q / ^X                quit (asks to save if modified)
 *   ESC                    cancel a prompt / clear selection
 */
#define _POSIX_C_SOURCE 200809L   /* getline, ssize_t */
#include <SDL2/SDL.h>
#include <SDL2/SDL_ttf.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>

#define SCREEN_W 320
#define SCREEN_H 170
#define TICK_MS  16

#define FONT_PATH_1 "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
#define FONT_PATH_2 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

#define FONT_PT   11
#define TITLE_H   14
#define STATUS_H  14

/* Colors. */
static const SDL_Color COL_BG     = {  18,  20,  26, 255 };
static const SDL_Color COL_TEXT   = { 220, 222, 228, 255 };
static const SDL_Color COL_BAR    = {  12,  14,  18, 255 };
static const SDL_Color COL_BAR_TX = { 150, 200, 255, 255 };
static const SDL_Color COL_ACCENT = {  90, 200, 140, 255 };
static const SDL_Color COL_SEL    = {  40,  70, 110, 255 };
static const SDL_Color COL_CURSOR = { 120, 230, 170, 255 };
static const SDL_Color COL_LINENO = {  80,  84,  96, 255 };

/* ---- text buffer: array of heap lines ---- */
typedef struct {
    char  *s;
    int    len;   /* bytes used (no NUL counted) */
    int    cap;
} Line;

typedef struct {
    Line  *lines;
    int    count;
    int    cap;
    int    dirty;
    char   path[256];
} Buffer;

static void line_reserve(Line *l, int need) {
    if (need <= l->cap) return;
    int cap = l->cap ? l->cap : 16;
    while (cap < need) cap *= 2;
    l->s = realloc(l->s, cap);
    l->cap = cap;
}
static void buf_reserve(Buffer *b, int need) {
    if (need <= b->cap) return;
    int cap = b->cap ? b->cap : 32;
    while (cap < need) cap *= 2;
    b->lines = realloc(b->lines, cap * sizeof(Line));
    b->cap = cap;
}
static void buf_init(Buffer *b) {
    memset(b, 0, sizeof(*b));
    buf_reserve(b, 1);
    b->lines[0] = (Line){0};
    b->count = 1;
}
static void buf_insert_line(Buffer *b, int at, const char *s, int len) {
    buf_reserve(b, b->count + 1);
    memmove(&b->lines[at + 1], &b->lines[at], (b->count - at) * sizeof(Line));
    b->lines[at] = (Line){0};
    if (len > 0) { line_reserve(&b->lines[at], len); memcpy(b->lines[at].s, s, len);
                   b->lines[at].len = len; }
    b->count++;
}
static void buf_free(Buffer *b) {
    for (int i = 0; i < b->count; i++) free(b->lines[i].s);
    free(b->lines);
    memset(b, 0, sizeof(*b));
}

/* insert bytes into a line at column col */
static void line_insert(Line *l, int col, const char *s, int len) {
    line_reserve(l, l->len + len);
    memmove(l->s + col + len, l->s + col, l->len - col);
    memcpy(l->s + col, s, len);
    l->len += len;
}
/* delete `len` bytes from a line at col */
static void line_delete(Line *l, int col, int len) {
    memmove(l->s + col, l->s + col + len, l->len - col - len);
    l->len -= len;
}

/* ---- editor state ---- */
typedef struct { int line, col; } Pos;

typedef struct {
    Buffer buf;
    Pos    cur;        /* cursor */
    Pos    sel;        /* selection anchor (== cur when no selection) */
    int    has_sel;
    int    top;        /* first visible line */
    int    left;       /* first visible column (horizontal scroll) */
    int    rows, cols; /* visible text grid */
    int    ch_w, ch_h; /* glyph cell size */

    TTF_Font *font;
    SDL_Renderer *ren;

    char   status[160];
    Uint32 status_until;

    /* prompt (save-as / confirm) */
    int    prompt_mode;   /* 0 none, 1 save-as, 2 confirm-quit */
    char   prompt[256];
    int    quit;
} Editor;

/* ---------- helpers ---------- */
static void set_status(Editor *e, const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    vsnprintf(e->status, sizeof(e->status), fmt, ap); va_end(ap);
    e->status_until = SDL_GetTicks() + 4000;
}

static int cmp_pos(Pos a, Pos b) {
    if (a.line != b.line) return a.line < b.line ? -1 : 1;
    if (a.col  != b.col)  return a.col  < b.col  ? -1 : 1;
    return 0;
}
/* ordered selection bounds; returns 1 if a real (non-empty) selection */
static int sel_range(Editor *e, Pos *lo, Pos *hi) {
    if (!e->has_sel) return 0;
    if (cmp_pos(e->cur, e->sel) <= 0) { *lo = e->cur; *hi = e->sel; }
    else                              { *lo = e->sel; *hi = e->cur; }
    return cmp_pos(*lo, *hi) != 0;
}

static void clamp_cursor(Editor *e) {
    if (e->cur.line < 0) e->cur.line = 0;
    if (e->cur.line >= e->buf.count) e->cur.line = e->buf.count - 1;
    int ll = e->buf.lines[e->cur.line].len;
    if (e->cur.col < 0) e->cur.col = 0;
    if (e->cur.col > ll) e->cur.col = ll;
}

static void ensure_visible(Editor *e) {
    if (e->cur.line < e->top) e->top = e->cur.line;
    if (e->cur.line >= e->top + e->rows) e->top = e->cur.line - e->rows + 1;
    if (e->top < 0) e->top = 0;
    if (e->cur.col < e->left) e->left = e->cur.col;
    if (e->cur.col >= e->left + e->cols) e->left = e->cur.col - e->cols + 1;
    if (e->left < 0) e->left = 0;
}

/* ---------- selection delete (used by cut / typing over a selection) ---------- */
static void delete_selection(Editor *e) {
    Pos lo, hi;
    if (!sel_range(e, &lo, &hi)) { e->has_sel = 0; return; }
    if (lo.line == hi.line) {
        line_delete(&e->buf.lines[lo.line], lo.col, hi.col - lo.col);
    } else {
        Line *first = &e->buf.lines[lo.line];
        Line *last  = &e->buf.lines[hi.line];
        first->len = lo.col;                               /* trim first line */
        line_insert(first, first->len, last->s + hi.col, last->len - hi.col);
        for (int i = lo.line + 1; i <= hi.line; i++) free(e->buf.lines[i].s);
        memmove(&e->buf.lines[lo.line + 1], &e->buf.lines[hi.line + 1],
                (e->buf.count - hi.line - 1) * sizeof(Line));
        e->buf.count -= (hi.line - lo.line);
    }
    e->cur = lo;
    e->has_sel = 0;
    e->buf.dirty = 1;
}

/* Serialize the current selection to a malloc'd NUL-terminated string. */
static char *selection_text(Editor *e) {
    Pos lo, hi;
    if (!sel_range(e, &lo, &hi)) return NULL;
    size_t n = 1;
    for (int i = lo.line; i <= hi.line; i++) {
        int a = (i == lo.line) ? lo.col : 0;
        int b = (i == hi.line) ? hi.col : e->buf.lines[i].len;
        n += (b - a) + 1; /* +1 for newline */
    }
    char *out = malloc(n), *p = out;
    for (int i = lo.line; i <= hi.line; i++) {
        int a = (i == lo.line) ? lo.col : 0;
        int b = (i == hi.line) ? hi.col : e->buf.lines[i].len;
        memcpy(p, e->buf.lines[i].s + a, b - a); p += (b - a);
        if (i != hi.line) *p++ = '\n';
    }
    *p = 0;
    return out;
}

/* ---------- editing primitives ---------- */
static void insert_text(Editor *e, const char *s, int len) {
    if (e->has_sel) delete_selection(e);
    for (int i = 0; i < len; ) {
        if (s[i] == '\n') {
            Line *l = &e->buf.lines[e->cur.line];
            int tail = l->len - e->cur.col;
            buf_insert_line(&e->buf, e->cur.line + 1, l->s + e->cur.col, tail);
            l = &e->buf.lines[e->cur.line];      /* realloc-safe re-fetch */
            l->len = e->cur.col;
            e->cur.line++; e->cur.col = 0;
            i++;
        } else {
            int j = i;
            while (j < len && s[j] != '\n') j++;
            line_insert(&e->buf.lines[e->cur.line], e->cur.col, s + i, j - i);
            e->cur.col += (j - i);
            i = j;
        }
    }
    e->buf.dirty = 1;
}

static void do_backspace(Editor *e) {
    if (e->has_sel) { delete_selection(e); return; }
    if (e->cur.col > 0) {
        line_delete(&e->buf.lines[e->cur.line], e->cur.col - 1, 1);
        e->cur.col--;
    } else if (e->cur.line > 0) {
        Line *prev = &e->buf.lines[e->cur.line - 1];
        Line *cur  = &e->buf.lines[e->cur.line];
        int at = prev->len;
        line_insert(prev, prev->len, cur->s, cur->len);
        free(cur->s);
        memmove(&e->buf.lines[e->cur.line], &e->buf.lines[e->cur.line + 1],
                (e->buf.count - e->cur.line - 1) * sizeof(Line));
        e->buf.count--;
        e->cur.line--; e->cur.col = at;
    }
    e->buf.dirty = 1;
}

static void do_delete(Editor *e) {
    if (e->has_sel) { delete_selection(e); return; }
    Line *l = &e->buf.lines[e->cur.line];
    if (e->cur.col < l->len) {
        line_delete(l, e->cur.col, 1);
    } else if (e->cur.line < e->buf.count - 1) {
        Line *next = &e->buf.lines[e->cur.line + 1];
        line_insert(l, l->len, next->s, next->len);
        free(next->s);
        memmove(&e->buf.lines[e->cur.line + 1], &e->buf.lines[e->cur.line + 2],
                (e->buf.count - e->cur.line - 2) * sizeof(Line));
        e->buf.count--;
    }
    e->buf.dirty = 1;
}

/* ---------- file IO ---------- */
static int load_file(Editor *e, const char *path) {
    FILE *f = fopen(path, "rb");
    snprintf(e->buf.path, sizeof(e->buf.path), "%s", path);
    if (!f) { set_status(e, "New file: %s", path); return 0; }
    buf_free(&e->buf); buf_init(&e->buf);
    e->buf.count = 0;
    char *linebuf = NULL; size_t cap = 0; ssize_t n;
    while ((n = getline(&linebuf, &cap, f)) >= 0) {
        int len = (int)n;
        while (len > 0 && (linebuf[len-1] == '\n' || linebuf[len-1] == '\r')) len--;
        buf_insert_line(&e->buf, e->buf.count, linebuf, len);
    }
    free(linebuf);
    fclose(f);
    if (e->buf.count == 0) buf_insert_line(&e->buf, 0, "", 0);
    snprintf(e->buf.path, sizeof(e->buf.path), "%s", path);
    e->buf.dirty = 0;
    set_status(e, "Read %d lines from %s", e->buf.count, path);
    return 1;
}

static int save_file(Editor *e, const char *path) {
    FILE *f = fopen(path, "wb");
    if (!f) { set_status(e, "Error: cannot write %s", path); return 0; }
    for (int i = 0; i < e->buf.count; i++) {
        fwrite(e->buf.lines[i].s, 1, e->buf.lines[i].len, f);
        fputc('\n', f);
    }
    fclose(f);
    snprintf(e->buf.path, sizeof(e->buf.path), "%s", path);
    e->buf.dirty = 0;
    set_status(e, "Wrote %d lines to %s", e->buf.count, path);
    return 1;
}

/* ---------- rendering ---------- */
static void draw_text(Editor *e, const char *s, int x, int y, SDL_Color c) {
    if (!s || !*s) return;
    SDL_Surface *surf = TTF_RenderUTF8_Blended(e->font, s, c);
    if (!surf) return;
    SDL_Texture *t = SDL_CreateTextureFromSurface(e->ren, surf);
    SDL_Rect d = { x, y, surf->w, surf->h };
    SDL_FreeSurface(surf);
    if (t) { SDL_RenderCopy(e->ren, t, NULL, &d); SDL_DestroyTexture(t); }
}

static void fill(Editor *e, int x, int y, int w, int h, SDL_Color c) {
    SDL_SetRenderDrawColor(e->ren, c.r, c.g, c.b, c.a);
    SDL_Rect r = { x, y, w, h };
    SDL_RenderFillRect(e->ren, &r);
}

static void render(Editor *e) {
    SDL_Renderer *r = e->ren;
    SDL_SetRenderDrawColor(r, COL_BG.r, COL_BG.g, COL_BG.b, 255);
    SDL_RenderClear(r);

    const int GUT = 4 * e->ch_w;             /* line-number gutter */
    const int text_x0 = GUT + 2;
    const int text_y0 = TITLE_H;

    /* title bar */
    fill(e, 0, 0, SCREEN_W, TITLE_H, COL_BAR);
    char title[300];
    snprintf(title, sizeof(title), "%s%s",
             e->buf.path[0] ? e->buf.path : "[new]",
             e->buf.dirty ? " *" : "");
    draw_text(e, title, 4, 0, COL_BAR_TX);

    /* selection bounds for highlight */
    Pos lo = {0}, hi = {0};
    int have_sel = sel_range(e, &lo, &hi);

    for (int row = 0; row < e->rows; row++) {
        int li = e->top + row;
        if (li >= e->buf.count) break;
        int y = text_y0 + row * e->ch_h;
        Line *l = &e->buf.lines[li];

        /* line number */
        char ln[16]; snprintf(ln, sizeof(ln), "%3d", li + 1);
        draw_text(e, ln, 2, y, COL_LINENO);

        /* selection highlight for this row */
        if (have_sel && li >= lo.line && li <= hi.line) {
            int a = (li == lo.line) ? lo.col : 0;
            int b = (li == hi.line) ? hi.col : l->len + 1; /* +1: include EOL */
            a -= e->left; b -= e->left;
            if (a < 0) a = 0;
            if (b > e->cols) b = e->cols;
            if (b > a)
                fill(e, text_x0 + a * e->ch_w, y, (b - a) * e->ch_w, e->ch_h, COL_SEL);
        }

        /* visible slice of the line */
        int start = e->left;
        if (start < l->len) {
            int n = l->len - start;
            if (n > e->cols) n = e->cols;
            char tmp[512];
            if (n > (int)sizeof(tmp) - 1) n = sizeof(tmp) - 1;
            memcpy(tmp, l->s + start, n); tmp[n] = 0;
            draw_text(e, tmp, text_x0, y, COL_TEXT);
        }
    }

    /* cursor */
    if (e->prompt_mode == 0) {
        int crow = e->cur.line - e->top;
        int ccol = e->cur.col - e->left;
        if (crow >= 0 && crow < e->rows && ccol >= 0 && ccol <= e->cols) {
            int cx = text_x0 + ccol * e->ch_w;
            int cy = text_y0 + crow * e->ch_h;
            if ((SDL_GetTicks() / 500) % 2 == 0)
                fill(e, cx, cy, 2, e->ch_h, COL_CURSOR);
        }
    }

    /* status bar */
    fill(e, 0, SCREEN_H - STATUS_H, SCREEN_W, STATUS_H, COL_BAR);
    if (e->prompt_mode) {
        char ps[300];
        snprintf(ps, sizeof(ps), "%s%s",
                 e->prompt_mode == 1 ? "Save as: " : "Save modified buffer? (y/n) ",
                 e->prompt);
        draw_text(e, ps, 4, SCREEN_H - STATUS_H, COL_ACCENT);
    } else if (e->status[0] && SDL_GetTicks() < e->status_until) {
        draw_text(e, e->status, 4, SCREEN_H - STATUS_H, COL_ACCENT);
    } else {
        char info[160];
        snprintf(info, sizeof(info), "^S save ^K cut ^C copy ^V paste ^Q quit  L%d:C%d",
                 e->cur.line + 1, e->cur.col + 1);
        draw_text(e, info, 4, SCREEN_H - STATUS_H, COL_BAR_TX);
    }

    SDL_RenderPresent(r);
}

/* ---------- movement ---------- */
static void move(Editor *e, int dline, int dcol, int shift) {
    if (shift && !e->has_sel) { e->sel = e->cur; e->has_sel = 1; }
    if (!shift) e->has_sel = 0;

    if (dcol == -1 && e->cur.col == 0 && e->cur.line > 0) {
        e->cur.line--; e->cur.col = e->buf.lines[e->cur.line].len;
    } else if (dcol == 1 && e->cur.col == e->buf.lines[e->cur.line].len &&
               e->cur.line < e->buf.count - 1) {
        e->cur.line++; e->cur.col = 0;
    } else {
        e->cur.col += dcol;
    }
    e->cur.line += dline;
    clamp_cursor(e);
    ensure_visible(e);
}

/* ---------- clipboard ---------- */
static void do_copy(Editor *e) {
    char *t = selection_text(e);
    if (t) { SDL_SetClipboardText(t); free(t); set_status(e, "Copied"); }
}
static void do_cut(Editor *e) {
    if (!e->has_sel) {  /* cut whole current line, nano-style */
        Line *l = &e->buf.lines[e->cur.line];
        e->sel = (Pos){ e->cur.line, 0 };
        e->cur = (Pos){ e->cur.line, l->len };
        e->has_sel = 1;
    }
    char *t = selection_text(e);
    if (t) { SDL_SetClipboardText(t); free(t); }
    delete_selection(e);
    ensure_visible(e);
    set_status(e, "Cut");
}
static void do_paste(Editor *e) {
    if (!SDL_HasClipboardText()) return;
    char *t = SDL_GetClipboardText();
    if (t) { insert_text(e, t, (int)strlen(t)); SDL_free(t); ensure_visible(e);
             set_status(e, "Pasted"); }
}
static void select_all(Editor *e) {
    e->sel = (Pos){ 0, 0 };
    e->cur = (Pos){ e->buf.count - 1, e->buf.lines[e->buf.count - 1].len };
    e->has_sel = 1;
    ensure_visible(e);
}

/* ---------- key handling ---------- */
static void handle_prompt_key(Editor *e, SDL_Keycode k) {
    if (e->prompt_mode == 1) { /* save-as */
        if (k == SDLK_RETURN || k == SDLK_KP_ENTER) {
            if (e->prompt[0]) save_file(e, e->prompt);
            e->prompt_mode = 0; SDL_StopTextInput();
        } else if (k == SDLK_ESCAPE) {
            e->prompt_mode = 0; SDL_StopTextInput(); set_status(e, "Cancelled");
        } else if (k == SDLK_BACKSPACE) {
            size_t n = strlen(e->prompt); if (n) e->prompt[n-1] = 0;
        }
    } else if (e->prompt_mode == 2) { /* confirm quit */
        if (k == SDLK_y) { e->prompt_mode = 0;
            if (e->buf.path[0]) { save_file(e, e->buf.path); e->quit = 1; }
            else { e->prompt_mode = 1; snprintf(e->prompt, sizeof(e->prompt), "%s", "");
                   SDL_StartTextInput(); } }
        else if (k == SDLK_n) { e->quit = 1; }
        else if (k == SDLK_ESCAPE) { e->prompt_mode = 0; }
    }
}

static void request_quit(Editor *e) {
    if (e->buf.dirty) { e->prompt_mode = 2; }
    else e->quit = 1;
}

static void handle_key(Editor *e, SDL_Keysym ks) {
    SDL_Keycode k = ks.sym;
    int ctrl  = (ks.mod & KMOD_CTRL)  != 0;
    int shift = (ks.mod & KMOD_SHIFT) != 0;

    if (e->prompt_mode) { handle_prompt_key(e, k); return; }

    if (ctrl) {
        switch (k) {
            case SDLK_s: if (e->buf.path[0]) save_file(e, e->buf.path);
                         else { e->prompt_mode = 1; e->prompt[0] = 0; SDL_StartTextInput(); }
                         return;
            case SDLK_o: e->prompt_mode = 1;
                         snprintf(e->prompt, sizeof(e->prompt), "%s", e->buf.path);
                         SDL_StartTextInput(); return;
            case SDLK_k: do_cut(e);   return;
            case SDLK_c: do_copy(e);  return;
            case SDLK_u: case SDLK_v: do_paste(e); return;
            case SDLK_g: select_all(e); return;
            case SDLK_a: e->cur.col = 0; e->has_sel = 0; ensure_visible(e); return;
            case SDLK_e: e->cur.col = e->buf.lines[e->cur.line].len;
                         e->has_sel = 0; ensure_visible(e); return;
            case SDLK_q: case SDLK_x: request_quit(e); return;
            default: return;
        }
    }

    switch (k) {
        case SDLK_LEFT:  move(e,  0, -1, shift); break;
        case SDLK_RIGHT: move(e,  0,  1, shift); break;
        case SDLK_UP:    move(e, -1,  0, shift); break;
        case SDLK_DOWN:  move(e,  1,  0, shift); break;
        case SDLK_PAGEUP:   e->cur.line -= e->rows; if (!shift) e->has_sel = 0;
                            else if (!e->has_sel){e->sel=e->cur;e->has_sel=1;}
                            clamp_cursor(e); ensure_visible(e); break;
        case SDLK_PAGEDOWN: e->cur.line += e->rows; if (!shift) e->has_sel = 0;
                            else if (!e->has_sel){e->sel=e->cur;e->has_sel=1;}
                            clamp_cursor(e); ensure_visible(e); break;
        case SDLK_HOME: e->cur.col = 0; e->has_sel = shift ? e->has_sel : 0; ensure_visible(e); break;
        case SDLK_END:  e->cur.col = e->buf.lines[e->cur.line].len;
                        e->has_sel = shift ? e->has_sel : 0; ensure_visible(e); break;
        case SDLK_RETURN: case SDLK_KP_ENTER: insert_text(e, "\n", 1); ensure_visible(e); break;
        case SDLK_TAB:    insert_text(e, "    ", 4); ensure_visible(e); break;
        case SDLK_BACKSPACE: do_backspace(e); ensure_visible(e); break;
        case SDLK_DELETE:    do_delete(e);    ensure_visible(e); break;
        case SDLK_ESCAPE:    e->has_sel = 0; break;
        default: break;
    }
}

static void handle_text(Editor *e, const char *t) {
    if (e->prompt_mode == 1) {
        size_t n = strlen(e->prompt);
        for (const char *p = t; *p && n < sizeof(e->prompt) - 1; p++)
            if ((unsigned char)*p >= 0x20) e->prompt[n++] = *p;
        e->prompt[n] = 0;
        return;
    }
    if (e->prompt_mode) return;
    insert_text(e, t, (int)strlen(t));
    ensure_visible(e);
}

int main(int argc, char **argv) {
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS) != 0) {
        fprintf(stderr, "SDL_Init: %s\n", SDL_GetError()); return 1;
    }
    if (TTF_Init() != 0) { fprintf(stderr, "TTF_Init: %s\n", TTF_GetError());
                           SDL_Quit(); return 1; }

    SDL_Window *win = SDL_CreateWindow("Editor",
        SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
        SCREEN_W, SCREEN_H, SDL_WINDOW_BORDERLESS);
    if (!win) { fprintf(stderr, "SDL_CreateWindow: %s\n", SDL_GetError());
                TTF_Quit(); SDL_Quit(); return 1; }
    SDL_Renderer *ren = SDL_CreateRenderer(win, -1, SDL_RENDERER_SOFTWARE);
    if (!ren) { fprintf(stderr, "SDL_CreateRenderer: %s\n", SDL_GetError());
                SDL_DestroyWindow(win); TTF_Quit(); SDL_Quit(); return 1; }

    Editor e;
    memset(&e, 0, sizeof(e));
    e.ren = ren;
    e.font = TTF_OpenFont(FONT_PATH_1, FONT_PT);
    if (!e.font) e.font = TTF_OpenFont(FONT_PATH_2, FONT_PT);
    if (!e.font) { fprintf(stderr, "TTF_OpenFont: %s\n", TTF_GetError());
                   SDL_DestroyRenderer(ren); SDL_DestroyWindow(win);
                   TTF_Quit(); SDL_Quit(); return 1; }

    /* monospace cell metrics */
    TTF_SizeUTF8(e.font, "M", &e.ch_w, &e.ch_h);
    if (e.ch_w < 1) e.ch_w = 6;
    if (e.ch_h < 1) e.ch_h = 12;
    const int GUT = 4 * e.ch_w;
    e.cols = (SCREEN_W - GUT - 2) / e.ch_w;
    e.rows = (SCREEN_H - TITLE_H - STATUS_H) / e.ch_h;

    buf_init(&e.buf);
    if (argc > 1) load_file(&e, argv[1]);
    else set_status(&e, "New buffer  -  ^S save  ^Q quit");

    SDL_StartTextInput();
    while (!e.quit) {
        Uint32 now = SDL_GetTicks();
        SDL_Event ev;
        while (SDL_PollEvent(&ev)) {
            if (ev.type == SDL_QUIT) request_quit(&e);
            else if (ev.type == SDL_KEYDOWN) handle_key(&e, ev.key.keysym);
            else if (ev.type == SDL_TEXTINPUT) handle_text(&e, ev.text.text);
        }
        render(&e);
        Uint32 el = SDL_GetTicks() - now;
        if (el < TICK_MS) SDL_Delay(TICK_MS - el);
    }
    SDL_StopTextInput();

    buf_free(&e.buf);
    TTF_CloseFont(e.font);
    SDL_DestroyRenderer(ren);
    SDL_DestroyWindow(win);
    TTF_Quit();
    SDL_Quit();
    return 0;
}
