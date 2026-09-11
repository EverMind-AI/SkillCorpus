PYTHON ?= python3

.PHONY: install-ci check-repo test package

install-ci:
	$(PYTHON) -m pip install -r requirements/ci.txt
	$(PYTHON) -m pip install --no-deps -e .

check-repo:
	$(PYTHON) scripts/check_repo_assets.py
	$(PYTHON) scripts/check_file_sizes.py
	$(PYTHON) scripts/check_project_inventory.py
	$(PYTHON) skillcorpus_plugin/scripts/verify_release_versions.py

test:
	$(PYTHON) -m pytest -q skillcorpus/tests

package:
	$(PYTHON) -m pip install -r requirements/build.txt
	$(PYTHON) -m build --no-isolation
	$(PYTHON) scripts/check_package_contents.py dist/*.whl
