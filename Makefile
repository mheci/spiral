.PHONY: test check-packages iso clean

PYTHON ?= python3

test:
	PYTHONPATH=installer $(PYTHON) -m unittest discover -s tests -t . -v

check-packages:
	$(PYTHON) builder/scripts/check-packages.py --root .

iso:
	docker run --rm --privileged \
		-v "$(CURDIR):/spiral" -w /spiral \
		-e SOURCE_DATE_EPOCH="$$(git log -1 --format=%ct)" \
		debian:trixie \
		bash -lc "./builder/scripts/install-build-deps.sh && ./builder/scripts/build-iso.sh --output /spiral/dist"

clean:
	rm -rf build dist
