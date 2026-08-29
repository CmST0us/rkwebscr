CXX ?= g++
PKGS = libpipewire-0.3 libdrm gbm rockchip_mpp
CXXFLAGS ?= -O3 -std=gnu++20 -Wall -Wextra

rkstream-dmabuf-encoder: rkstream-dmabuf-encoder.cpp
	$(CXX) $(CXXFLAGS) $(shell pkg-config --cflags $(PKGS)) $< -o $@ $(shell pkg-config --libs $(PKGS)) -lyuv

.PHONY: clean
clean:
	rm -f rkstream-dmabuf-encoder
