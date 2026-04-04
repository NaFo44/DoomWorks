PLATFORM ?= simulator
WAD ?= doom1.wad
USE_EXTERNAL_IWAD ?= 0
USE_UNSTABLE_ZONE_HEAP_SIZE ?= 0

NUMWORKS_APP_DIR ?= $(CURDIR)/numworks_app

HOST_OS := $(shell uname -s)
ifeq ($(HOST_OS),Darwin)
SIM_PLATFORM := macos
SIM_BIN ?= $(CURDIR)/epsilon.app/Contents/MacOS/Epsilon
else
SIM_PLATFORM := linux
SIM_BIN ?= $(CURDIR)/epsilon.bin
endif

NWB_FILE := $(NUMWORKS_APP_DIR)/output/$(SIM_PLATFORM)/gbadoom.nwb
NWA_FILE := $(NUMWORKS_APP_DIR)/output/device/gbadoom.nwa
WAD_ABS := $(abspath $(WAD))
WAD_BASENAME := $(notdir $(WAD))
IWAD_EMBED_NAME := $(subst -,_,$(basename $(WAD_BASENAME)))
IWAD_C_FILE := $(CURDIR)/source/iwad/$(IWAD_EMBED_NAME).c
IWAD_INCLUDE_DEFINE := -DEMBEDDED_IWAD_INCLUDE=\"iwad/$(IWAD_EMBED_NAME).c\"
BUILD_CFG_FILE := $(NUMWORKS_APP_DIR)/output/.gbadoom_build_cfg_$(PLATFORM)

# Size optimization flags (always on)
GBADOOM_OPT_FLAGS := -Os

# Optional stack slot reuse (can expose UB in legacy code, keep off by default)
GBADOOM_ENABLE_STACK_REUSE ?= 0
ifeq ($(GBADOOM_ENABLE_STACK_REUSE),1)
GBADOOM_OPT_FLAGS += -fstack-reuse=all
endif

.PHONY: build run clean

$(IWAD_C_FILE): $(WAD_ABS) $(CURDIR)/GbaWadUtil/GbaWadUtil
	@mkdir -p $(dir $@)
	@echo "[GBADOOM] Embedding $(WAD_BASENAME) into $(notdir $@)"
	@$(CURDIR)/GbaWadUtil/GbaWadUtil -in "$(WAD_ABS)" -cfile "$@"

ifeq ($(PLATFORM),device)
ifeq ($(USE_EXTERNAL_IWAD),1)
NEEDS_EMBEDDED_IWAD := 0
else
NEEDS_EMBEDDED_IWAD := 1
endif
else
NEEDS_EMBEDDED_IWAD := 1
endif

build:
	@if [ "$(NEEDS_EMBEDDED_IWAD)" = "1" ]; then \
		$(MAKE) $(IWAD_C_FILE); \
	fi
	@mkdir -p $(NUMWORKS_APP_DIR)/output
	@if [ -f "$(BUILD_CFG_FILE)" ]; then \
		old_cfg=$$(cat "$(BUILD_CFG_FILE)"); \
		new_cfg="$(PLATFORM)|$(USE_EXTERNAL_IWAD)|$(WAD_BASENAME)"; \
		if [ "$$old_cfg" != "$$new_cfg" ]; then \
			echo "[NUMWORKS] Config changed ($$old_cfg -> $$new_cfg), cleaning stale objects"; \
			$(MAKE) -C $(NUMWORKS_APP_DIR) PLATFORM=$(PLATFORM) clean; \
		fi; \
	fi
	@echo "$(PLATFORM)|$(USE_EXTERNAL_IWAD)|$(WAD_BASENAME)" > "$(BUILD_CFG_FILE)"
ifeq ($(PLATFORM),device)
	$(MAKE) -C $(NUMWORKS_APP_DIR) PLATFORM=device EXTERNAL_DATA=$(WAD_ABS) USE_EXTERNAL_IWAD=$(USE_EXTERNAL_IWAD) USE_UNSTABLE_ZONE_HEAP_SIZE=$(USE_UNSTABLE_ZONE_HEAP_SIZE) EXTRA_CFLAGS='$(GBADOOM_OPT_FLAGS) $(IWAD_INCLUDE_DEFINE)' EXTRA_CXXFLAGS='$(GBADOOM_OPT_FLAGS) $(IWAD_INCLUDE_DEFINE)' build
else
	$(MAKE) -C $(NUMWORKS_APP_DIR) PLATFORM=simulator SIMULATOR="$(SIM_BIN)" EXTERNAL_DATA=$(WAD_ABS) USE_EXTERNAL_IWAD=$(USE_EXTERNAL_IWAD) USE_UNSTABLE_ZONE_HEAP_SIZE=$(USE_UNSTABLE_ZONE_HEAP_SIZE) EXTRA_CFLAGS='$(GBADOOM_OPT_FLAGS) $(IWAD_INCLUDE_DEFINE)' EXTRA_CXXFLAGS='$(GBADOOM_OPT_FLAGS) $(IWAD_INCLUDE_DEFINE)' build
endif

run: build
ifeq ($(PLATFORM),device)
ifeq ($(USE_EXTERNAL_IWAD),1)
	npx --yes -- nwlink install-nwa --external-data $(WAD_ABS) $(NWA_FILE)
else
	npx --yes -- nwlink install-nwa $(NWA_FILE)
endif
else
	$(SIM_BIN) --nwb $(NWB_FILE) --nwb-external-data $(WAD_ABS)
endif

clean:
	$(MAKE) -C $(NUMWORKS_APP_DIR) PLATFORM=simulator clean
	$(MAKE) -C $(NUMWORKS_APP_DIR) PLATFORM=device clean
