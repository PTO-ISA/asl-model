PYTHON ?= python3
BUILD_DIR ?= build

.PHONY: test check

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -p 'test_*.py'
	cmake -S . -B $(BUILD_DIR) -DCMAKE_BUILD_TYPE=Release
	cmake --build $(BUILD_DIR) -j 8
	ctest --test-dir $(BUILD_DIR) --output-on-failure

check: test
	PYTHONPATH=src $(PYTHON) -m compileall -q src scripts tools
	git diff --check
