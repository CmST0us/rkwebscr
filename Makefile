.PHONY: all native check clean

all: native

native:
	$(MAKE) -C native

check:
	sh tests/smoke.sh

clean:
	$(MAKE) -C native clean

