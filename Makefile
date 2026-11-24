# make install            installs for the current user, into ~/.local
# make install PREFIX=/usr DESTDIR=...   for packaging
PREFIX  ?= $(HOME)/.local
DESTDIR ?=
APP_ID  := io.github.antonstroberg.Glaide

share := $(DESTDIR)$(PREFIX)/share

.PHONY: install uninstall test

install:
	install -Dm755 glaide.py "$(share)/glaide/glaide.py"
	install -Dm755 hook.sh "$(share)/glaide/hook.sh"
	install -Dm755 bin/glaide "$(DESTDIR)$(PREFIX)/bin/glaide"
	install -d "$(share)/applications" "$(share)/dbus-1/services"
	sed 's|@PREFIX@|$(PREFIX)|g' data/$(APP_ID).desktop.in > "$(share)/applications/$(APP_ID).desktop"
	sed 's|@PREFIX@|$(PREFIX)|g' data/$(APP_ID).Screenshot.desktop.in > "$(share)/applications/$(APP_ID).Screenshot.desktop"
	sed 's|@PREFIX@|$(PREFIX)|g' data/$(APP_ID).service.in > "$(share)/dbus-1/services/$(APP_ID).service"
	@echo
	@echo "Installed. Start it with: glaide"
	@echo "On KDE Plasma, run 'kbuildsycoca6' once so Meta+C and Meta+Shift+C are picked up."

uninstall:
	rm -rf "$(share)/glaide"
	rm -f "$(DESTDIR)$(PREFIX)/bin/glaide"
	rm -f "$(share)/applications/$(APP_ID).desktop" "$(share)/applications/$(APP_ID).Screenshot.desktop"
	rm -f "$(share)/dbus-1/services/$(APP_ID).service"

test:
	python3 -m unittest discover -s tests -v
