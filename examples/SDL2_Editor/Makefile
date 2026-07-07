CC       ?= gcc
CFLAGS   ?= -std=c11 -Wall -Wextra -O2
PKGS     := sdl2 SDL2_ttf
PKG_CFLAGS := $(shell pkg-config --cflags $(PKGS))
PKG_LIBS   := $(shell pkg-config --libs $(PKGS))

TARGET := editor
SRC    := main.c

all: $(TARGET)

$(TARGET): $(SRC)
	$(CC) $(CFLAGS) $(PKG_CFLAGS) $(SRC) -o $(TARGET) $(PKG_LIBS)

clean:
	rm -f $(TARGET)

.PHONY: all clean
