PYTHON ?= python3
BUILD_DIR ?= build
PACKAGE_BUILD_DIR ?= $(BUILD_DIR)-package-consumer
PACKAGE_SHARED_BUILD_DIR ?= $(PACKAGE_BUILD_DIR)-shared
PACKAGE_STATIC_BUILD_DIR ?= $(PACKAGE_BUILD_DIR)-static
PACKAGE_INSTALL_DIR ?= $(BUILD_DIR)-install

.PHONY: test package-check check closure-check ndf-check

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -p 'test_*.py'
	cmake -S . -B $(BUILD_DIR) -DCMAKE_BUILD_TYPE=Release
	cmake --build $(BUILD_DIR) -j 8
	ctest --test-dir $(BUILD_DIR) --output-on-failure

package-check: test
	cmake --install $(BUILD_DIR) --prefix "$(abspath $(PACKAGE_INSTALL_DIR))"
	cmake -S tests/cmake_package_consumer/shared -B $(PACKAGE_SHARED_BUILD_DIR) \
		-DCMAKE_BUILD_TYPE=Release \
		-DCMAKE_PREFIX_PATH="$(abspath $(PACKAGE_INSTALL_DIR))"
	cmake --build $(PACKAGE_SHARED_BUILD_DIR) -j 8
	ctest --test-dir $(PACKAGE_SHARED_BUILD_DIR) --output-on-failure
	cmake -S tests/cmake_package_consumer/static -B $(PACKAGE_STATIC_BUILD_DIR) \
		-DCMAKE_BUILD_TYPE=Release \
		-DCMAKE_PREFIX_PATH="$(abspath $(PACKAGE_INSTALL_DIR))"
	cmake --build $(PACKAGE_STATIC_BUILD_DIR) -j 8
	ctest --test-dir $(PACKAGE_STATIC_BUILD_DIR) --output-on-failure

check: package-check
	PYTHONPATH=src $(PYTHON) -m compileall -q src scripts tools
	git diff --check

closure-check:
	PYTHONPATH=src $(PYTHON) -m unittest tests.test_closure_artifacts -v

ndf-check:
	tools/ndf/scripts/ndf check --root . --format json
