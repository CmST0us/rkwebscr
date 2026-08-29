.PHONY: all native check clean deb

all: native

native:
	$(MAKE) -C native

check:
	sh tests/smoke.sh

clean:
	$(MAKE) -C native clean

deb:
	dpkg-buildpackage --build=binary --no-sign
