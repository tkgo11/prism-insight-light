.PHONY: validate test install-user install-user-standalone install-project install-project-standalone uninstall-user uninstall-user-standalone

validate:
	python3 scripts/validate_skill.py SKILL.md
	python3 scripts/validate_skill.py standalone/SKILL.md

test: validate
	bash tests/test_installers.sh

install-user:
	bash ./install.sh --scope user

install-user-standalone:
	bash ./install.sh --variant standalone --scope user

install-project:
	@test -n "$(PROJECT_DIR)" || (echo "PROJECT_DIR is required" >&2; exit 1)
	bash ./install.sh --scope project --project-dir "$(PROJECT_DIR)"

install-project-standalone:
	@test -n "$(PROJECT_DIR)" || (echo "PROJECT_DIR is required" >&2; exit 1)
	bash ./install.sh --variant standalone --scope project --project-dir "$(PROJECT_DIR)"

uninstall-user:
	bash ./uninstall.sh --scope user

uninstall-user-standalone:
	bash ./uninstall.sh --variant standalone --scope user
