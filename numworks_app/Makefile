APP_NAME = gbadoom
APP_ICON = icon.png

OUTPUT_DIR = output

GBADOOM_ROOT ?= ..
EXTERNAL_DATA ?= $(GBADOOM_ROOT)/doom1.wad
USE_EXTERNAL_IWAD ?= 0
USE_UNSTABLE_ZONE_HEAP_SIZE ?= 0
GBADOOM_DISABLE_RECIP_TABLE ?= 0
EXTRA_LDFLAGS ?=

ALL_C_SOURCES := $(notdir $(wildcard $(GBADOOM_ROOT)/source/*.c))
ALL_CPP_SOURCES := $(notdir $(wildcard $(GBADOOM_ROOT)/source/*.cpp))
VPATH += $(GBADOOM_ROOT)/source

SOURCES = $(filter-out i_system_gba.cpp i_system_e32.cpp,$(ALL_C_SOURCES) $(ALL_CPP_SOURCES))
ifeq ($(GBADOOM_DISABLE_RECIP_TABLE),1)
SOURCES := $(filter-out m_recip.c,$(SOURCES))
endif

include build/external_app.mak

CFLAGS += -DNUMWORKS -I$(GBADOOM_ROOT)/include
CXXFLAGS += -DNUMWORKS -I$(GBADOOM_ROOT)/include
CFLAGS += -DUSE_EXTERNAL_IWAD=$(USE_EXTERNAL_IWAD)
CXXFLAGS += -DUSE_EXTERNAL_IWAD=$(USE_EXTERNAL_IWAD)
CFLAGS += -DUSE_UNSTABLE_ZONE_HEAP_SIZE=$(USE_UNSTABLE_ZONE_HEAP_SIZE)
CXXFLAGS += -DUSE_UNSTABLE_ZONE_HEAP_SIZE=$(USE_UNSTABLE_ZONE_HEAP_SIZE)
CFLAGS += $(EXTRA_CFLAGS)
CXXFLAGS += $(EXTRA_CXXFLAGS)
LDFLAGS += $(EXTRA_LDFLAGS)
CFLAGS += -std=gnu11
CFLAGS += -Wno-error -Wno-incompatible-pointer-types -Wno-implicit-function-declaration -Wno-builtin-declaration-mismatch
CXXFLAGS += -Wno-error
CXXFLAGS := $(filter-out -std=gnu11 -Wno-incompatible-pointer-types -Wno-implicit-function-declaration -Wno-builtin-declaration-mismatch,$(CXXFLAGS))
