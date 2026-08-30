CXX ?= g++
PKGS = libpipewire-0.3 libdrm rockchip_mpp
CXXFLAGS ?= -O3 -std=gnu++20 -Wall -Wextra
RGA_LIBS ?= -Wl,-l:librga.so.2

rkwebscr-dmabuf-encoder: rkwebscr-dmabuf-encoder.cpp
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(shell pkg-config --cflags $(PKGS)) $< -o $@ $(LDFLAGS) $(shell pkg-config --libs $(PKGS)) $(RGA_LIBS)

.PHONY: clean
clean:
	rm -f rkwebscr-dmabuf-encoder
