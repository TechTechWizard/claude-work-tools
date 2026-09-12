.PHONY: help check install

help:
	@echo "make check    Run claude-setup check over this repository"
	@echo "make install  Link the tools into ~/.local/bin"

# Needs the checker: uv tool install git+ssh://git@github.com/TechTechWizard/claude-setup-kit
check:
	@claude-setup check .

install:
	@./install.sh
