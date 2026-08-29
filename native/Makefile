CXX ?= g++
PKGS = libpipewire-0.3 libdrm rockchip_mpp
CXXFLAGS ?= -O3 -std=gnu++20 -Wall -Wextra
GBM_CFLAGS ?= $(shell pkg-config --cflags gbm 2>/dev/null)
GBM_LIBS ?= $(shell pkg-config --libs gbm 2>/dev/null || printf '%s' -lgbm)
LIBYUV_CFLAGS ?= $(shell pkg-config --cflags libyuv 2>/dev/null)
LIBYUV_LIBS ?= $(shell pkg-config --libs libyuv 2>/dev/null || printf '%s' -lyuv)

rkstream-dmabuf-encoder: rkstream-dmabuf-encoder.cpp
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(shell pkg-config --cflags $(PKGS)) $(GBM_CFLAGS) $(LIBYUV_CFLAGS) $< -o $@ $(LDFLAGS) $(shell pkg-config --libs $(PKGS)) $(GBM_LIBS) $(LIBYUV_LIBS)

.PHONY: clean
clean:
	rm -f rkstream-dmabuf-encoder
